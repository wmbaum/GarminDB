#!/usr/bin/env python

#
# copyright Tom Goetz
#

import os, logging, datetime, time

from sqlalchemy import *
from sqlalchemy.ext.declarative import *
from sqlalchemy.exc import *
from sqlalchemy.orm import *
from sqlalchemy.orm.attributes import *

from Fit import Conversions


logger = logging.getLogger(__name__)


class DB(object):

    max_commit_attempts = 5
    commit_errors = 0

    def __init__(self, db_params_dict, debug=False):
        logger.debug("DB %s debug %s ", repr(db_params_dict), str(debug))
        url_func = getattr(self, db_params_dict['db_type'] + '_url')
        if debug > 0:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
        self.engine = create_engine(url_func(db_params_dict), echo=(debug > 1))
        self.session_maker = sessionmaker(bind=self.engine)
        self._query_session = None

    @classmethod
    def sqlite_url(cls, db_params_dict):
        return "sqlite:///" + db_params_dict['db_path'] +  '/' + cls.db_name + '.db'

    @classmethod
    def mysql_url(cls, db_params_dict):
        return "mysql+pymysql://%s:%s@%s/%s" % (db_params_dict['db_username'], db_params_dict['db_password'], db_params_dict['db_host'], cls.db_name)

    def session(self):
        return self.session_maker()

    def query_session(self):
        if self._query_session is None:
            self._query_session = self.session()
        return self._query_session

    @classmethod
    def commit(cls, session):
        attempts = 0
        while attempts < DB.max_commit_attempts:
            try:
                session.commit()
                session.close()
                return
            except OperationalError as e:
                attempts += 1
                logger.error("Exeption '%s' on commit %s attempt %d" % (str(e), str(session), attempts))
                session.rollback()
                cls.commit_errors += 1
                time.sleep(attempts)
                continue
            break
        raise IOError("Failed to commit")


#
####
#

class DBObject(object):

    # defaults, overridden by subclasses
    time_col_name = None
    time_col = None
    match_cols = None
    match_col_names = None
    min_row_values = 1

    @classmethod
    def get_col_names(cls):
        return [col.name for col in cls.__table__.columns]

    @classmethod
    def get_col_by_name(cls, name):
        for col in cls.__table__._columns:
            if col.name == name:
                return col

    @classmethod
    def setup(cls):
        if cls.time_col_name:
            cls.time_col = cls.get_col_by_name(cls.time_col_name)
        if  cls.match_col_names is not None:
            cls.match_cols = {col_name : cls.get_col_by_name(col_name) for col_name in cls.match_col_names}
        else:
            cls.match_cols = {cls.time_col_name : cls.time_col}
        for match_col_name, match_col in cls.match_cols.iteritems():
            if match_col is None:
                raise ValueError('match_col is None')

    def set_col_value(self, name, value):
        if name in self.get_col_names():
            set_attribute(self, name, value)

    def _from_dict(self, values_dict, ignore_none=False):
        self.not_none_values = 0
        for key, value in values_dict.iteritems():
            if value is not None:
                self.not_none_values += 1
                self.set_col_value(key, value)
            elif not ignore_none:
                self.set_col_value(key, value)
        return self

    @classmethod
    def from_dict(cls, values_dict):
        return cls()._from_dict(values_dict)

    @classmethod
    def _delete_view(cls, db, view_name):
        db.engine.execute('DROP VIEW IF EXISTS ' + view_name)

    @classmethod
    def _create_view(cls, db, view_name, query_str):
        cls._delete_view(db, view_name)
        db.engine.execute('CREATE VIEW IF NOT EXISTS ' + view_name + ' AS ' + query_str)

    @classmethod
    def create_join_view(cls, db, view_name, join_table):
        query = db.session().query(cls, join_table).join(join_table)
        cls._create_view(db, view_name, str(query))

    @classmethod
    def _filter_columns(cls, values_dict):
        return {col : value for col, value in values_dict.iteritems() if col in cls.get_col_names()}

    @classmethod
    def matches(cls, values_dict):
        return len(cls._filter_columns(values_dict)) >= cls.min_row_values

    @classmethod
    def _find_query(cls, session, values_dict):
        query = session.query(cls)
        for match_col_name, match_col in cls.match_cols.iteritems():
            query = query.filter(match_col == values_dict[match_col_name])
        return query

    @classmethod
    def find_all(cls, db, values_dict):
        logger.debug("%s::find_all %s", cls.__name__, repr(values_dict))
        return cls._find_query(db.query_session(), values_dict).all()

    @classmethod
    def _find_one(cls, session, values_dict):
        logger.debug("%s::_find_one %s", cls.__name__, repr(values_dict))
        return cls._find_query(session, values_dict).one_or_none()

    @classmethod
    def find_one(cls, db, values_dict):
        logger.debug("%s::find_one %s", cls.__name__, repr(values_dict))
        return cls._find_one(db.query_session(), values_dict)

    @classmethod
    def find_id(cls, db, values_dict):
        logger.debug("%s::find_id %s", cls.__name__, repr(values_dict))
        instance = cls.find_one(db, values_dict)
        if instance is not None:
            return instance.id

    @classmethod
    def _create(cls, db, session, values_dict, ignore_none=False):
        logger.debug("%s::_create %s", cls.__name__, repr(values_dict))
        instance = cls.from_dict(values_dict)
        if instance.not_none_values < cls.min_row_values:
            if ignore_none:
                return None
            raise ValueError("%d not-None values: %s", instance.not_none_values, repr(values_dict))
        session.add(instance)

    @classmethod
    def create(cls, db, values_dict, ignore_none=False):
        logger.debug("%s::create %s", cls.__name__, repr(values_dict))
        session = db.session()
        cls._create(db, session, values_dict)
        DB.commit(session)

    @classmethod
    def find_or_create(cls, db, values_dict):
        logger.debug("%s::find_or_create %s" % (cls.__name__, repr(values_dict)))
        session = db.session()
        if cls._find_one(session, values_dict) is None:
            cls._create(db, session, values_dict)
            DB.commit(session)

    @classmethod
    def create_or_update(cls, db, values_dict, ignore_none=False):
        logger.debug("%s::create_or_update %s", cls.__name__, repr(values_dict))
        session = db.session()
        instance = cls._find_one(session, values_dict)
        if instance is None:
            cls._create(db, session, values_dict, ignore_none)
        else:
            instance._from_dict(values_dict, ignore_none)
        DB.commit(session)

    @classmethod
    def create_or_update_not_none(cls, db, values_dict):
        logger.debug("%s::create_or_update_not_none %s", cls.__name__, repr(values_dict))
        cls.create_or_update(db, values_dict, True)

    @classmethod
    def row_to_int(cls, row):
        return int(row[0])

    @classmethod
    def row_to_int_not_none(cls, row):
        if row[0] is not None:
            return int(row[0])

    @classmethod
    def rows_to_ints(cls, rows):
        return [cls.row_to_int(row) for row in rows]

    @classmethod
    def rows_to_ints_not_none(cls, rows):
        return [cls.row_to_int_not_none(row) for row in rows]

    @classmethod
    def row_to_month(cls, row):
        return datetime.date(1900, row, 1).strftime("%b")

    @classmethod
    def rows_to_months(cls, rows):
        return [cls.row_to_month(row) for row in rows]

    @classmethod
    def get_years(cls, db):
        return cls.rows_to_ints_not_none(db.session().query(extract('year', cls.time_col)).distinct().all())

    @classmethod
    def get_months(cls, db, year):
          return cls.rows_to_ints_not_none(db.query_session().query(extract('month', cls.time_col)).filter(extract('year', cls.time_col) == str(year)).distinct().all())

    @classmethod
    def get_month_names(cls, db, year):
          return cls.rows_to_months(cls.get_months(db, year))

    @classmethod
    def get_days(cls, db, year):
        return cls.rows_to_ints(db.session().query(func.strftime("%j", cls.time_col)).filter(extract('year', cls.time_col) == str(year)).distinct().all())

    @classmethod
    def get_for_period(cls, db, table, start_ts, end_ts):
        query = db.query_session().query(table).filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts).order_by(cls.time_col)
        return query.all()

    @classmethod
    def get_for_day(cls, db, table, day_date):
        start_ts = datetime.datetime.combine(day_date, datetime.time.min)
        end_ts = start_ts + datetime.timedelta(1)
        return cls.get_for_period(db, table, start_ts, end_ts)

    @classmethod
    def get_col_for_period(cls, db, col, start_ts, end_ts):
        query = db.query_session().query(col).filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts).order_by(cls.time_col)
        return query.all()

    @classmethod
    def get_col_values(cls, db, get_col, match_col, match_value, start_ts=None, end_ts=None):
        query = db.query_session().query(get_col).order_by(cls.time_col)
        if start_ts is not None:
            query = query.filter(cls.time_col >= start_ts)
        if end_ts is not None:
            query = query.filter(cls.time_col < end_ts)
        query = query.filter(match_col == match_value)
        return query.all()

    @classmethod
    def get_col_distinct(cls, db, col, start_ts=None, end_ts=None, ignore_le_zero=False):
        query = db.query_session().query(distinct(col))
        if start_ts is not None:
            query = query.filter(cls.time_col >= start_ts)
        if end_ts is not None:
            query = query.filter(cls.time_col < end_ts)
        if ignore_le_zero:
            query = query.filter(col > 0)
        rows = query.all()
        return [row[0] for row in rows]

    @classmethod
    def get_col_func(cls, db, col, func, start_ts=None, end_ts=None, ignore_le_zero=False):
        query = db.query_session().query(func(col))
        if start_ts is not None:
            query = query.filter(cls.time_col >= start_ts)
        if end_ts is not None:
            query = query.filter(cls.time_col < end_ts)
        if ignore_le_zero:
            query = query.filter(col > 0)
        return query.scalar()

    @classmethod
    def get_col_avg(cls, db, col, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func(db, col, func.avg, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_min(cls, db, col, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func(db, col, func.min, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_max(cls, db, col, start_ts=None, end_ts=None):
        return cls.get_col_func(db, col, func.max, start_ts, end_ts, False)

    @classmethod
    def get_col_sum(cls, db, col, start_ts=None, end_ts=None):
        return cls.get_col_func(db, col, func.sum, start_ts, end_ts, False)

    @classmethod
    def get_col_latest(cls, db, col):
        return db.query_session().query(col).order_by(desc(cls.time_col)).limit(1).scalar()

    @classmethod
    def get_col_func_of_max_per_day(cls, db, col, stat_func, start_ts, end_ts):
        max_daily_query = (
            db.query_session().query(func.max(col).label('maxes'))
                .filter(cls.timestamp >= start_ts)
                .filter(cls.timestamp < end_ts)
                .group_by(func.strftime("%j", cls.timestamp))
        )
        return db.query_session().query(stat_func(max_daily_query.subquery().columns.maxes)).scalar()

    @classmethod
    def get_col_sum_of_max_per_day(cls, db, col, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day(db, col, func.sum, start_ts, end_ts)

    @classmethod
    def get_col_avg_of_max_per_day(cls, db, col, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day(db, col, func.avg, start_ts, end_ts)

    @classmethod
    def get_col_min_of_max_per_day(cls, db, col, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day(db, col, func.min, start_ts, end_ts)

    @classmethod
    def get_col_max_of_max_per_day(cls, db, col, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day(db, col, func.max, start_ts, end_ts)

    @classmethod
    def get_col_func_of_max_per_day_for_value(cls, db, col, stat_func, match_col, match_value, start_ts, end_ts):
        max_daily_query = (
            db.query_session().query(func.max(col).label('maxes'))
                .filter(match_col == match_value)
                .filter(cls.timestamp >= start_ts)
                .filter(cls.timestamp < end_ts)
                .group_by(func.strftime("%j", cls.timestamp))
        )
        return db.query_session().query(stat_func(max_daily_query.subquery().columns.maxes)).scalar()

    @classmethod
    def get_col_sum_of_max_per_day_for_value(cls, db, col, match_col, match_value, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day_for_value(db, col, func.sum, match_col, match_value, start_ts, end_ts)

    @classmethod
    def get_col_avg_of_max_per_day_for_value(cls, db, col, match_col, match_value, start_ts, end_ts):
       return cls.get_col_func_of_max_per_day_for_value(db, col, func.avg, match_col, match_value, start_ts, end_ts)

    @classmethod
    def get_time_col_func(cls, db, col, stat_func, start_ts=None, end_ts=None, ignore_le_zero=False):
        query = db.query_session().query(stat_func(func.strftime('%s', col) - func.strftime('%s', '00:00')))
        if start_ts is not None:
            query = query.filter(cls.time_col >= start_ts)
        if end_ts is not None:
            query = query.filter(cls.time_col < end_ts)
        if ignore_le_zero:
            query = query.filter(col > 0)
        return Conversions.secs_to_dt_time(query.scalar())

    @classmethod
    def get_time_col_avg(cls, db, col, start_ts, end_ts, ignore_le_zero=False):
        return cls.get_time_col_func(db, col, func.avg, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_time_col_min(cls, db, col, start_ts, end_ts, ignore_le_zero=False):
        return cls.get_time_col_func(db, col, func.min, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_time_col_max(cls, db, col, start_ts=None, end_ts=None):
        return cls.get_time_col_func(db, col, func.max, start_ts, end_ts)

    @classmethod
    def get_time_col_sum(cls, db, col, start_ts, end_ts):
        return cls.get_time_col_func(db, col, func.sum, start_ts, end_ts)

    @classmethod
    def latest_time(cls, db):
        return cls.get_col_max(db, cls.time_col)

    @classmethod
    def row_count(cls, db, col=None, col_value=None):
        query = db.query_session().query(cls)
        if col and col_value:
            query = query.filter(col == col_value)
        return query.count()

    @classmethod
    def row_count_for_period(cls, db, start_ts, end_ts):
        query = db.query_session().query(cls).filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts)
        return query.count()

    @classmethod
    def row_count_for_day(cls, db, day_date):
        start_ts = datetime.datetime.combine(day_date, datetime.time.min)
        end_ts = start_ts + datetime.timedelta(1)
        return cls.row_count_for_period(db, start_ts, end_ts)

    @classmethod
    def get_col_func_for_value(cls, db, col, stat_func, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        values_query = db.query_session().query(stat_func(col)).filter(match_col == match_value)
        if start_ts is not None or end_ts is not None:
            values_query = values_query.filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts)
        if ignore_le_zero:
            values_query = values_query.filter(col > 0)
        return values_query.scalar()

    @classmethod
    def get_col_sum_for_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_for_value(db, col, func.sum, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_avg_for_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_for_value(db, col, func.avg, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_min_for_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_for_value(db, col, func.min, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_max_for_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_for_value(db, col, func.max, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_func_greater_than_value(cls, db, col, stat_func, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        values_query = db.query_session().query(stat_func(col)).filter(match_col > match_value)
        if start_ts is not None or end_ts is not None:
            values_query = values_query.filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts)
        if ignore_le_zero:
            values_query = values_query.filter(col > 0)
        return values_query.scalar()

    @classmethod
    def get_col_avg_greater_than_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_greater_than_value(db, col, func.avg, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_func_less_than_value(cls, db, col, stat_func, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        values_query = db.query_session().query(stat_func(col)).filter(match_col < match_value)
        if start_ts is not None or end_ts is not None:
            values_query = values_query.filter(cls.time_col >= start_ts).filter(cls.time_col < end_ts)
        if ignore_le_zero:
            values_query = values_query.filter(col > 0)
        return values_query.scalar()

    @classmethod
    def get_col_avg_less_than_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_less_than_value(db, col, func.avg, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_min_less_than_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_less_than_value(db, col, func.min, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_col_max_less_than_value(cls, db, col, match_col, match_value, start_ts=None, end_ts=None, ignore_le_zero=False):
        return cls.get_col_func_less_than_value(db, col, func.max, match_col, match_value, start_ts, end_ts, ignore_le_zero)

    @classmethod
    def get_daily_stats(cls, db, day_ts):
        stats = cls.get_stats(db, day_ts, day_ts + datetime.timedelta(1))
        stats['day'] = day_ts
        return stats

    @classmethod
    def get_weekly_stats(cls, db, first_day_ts):
        stats = cls.get_stats(db, first_day_ts, first_day_ts + datetime.timedelta(7))
        stats['first_day'] = first_day_ts
        return stats

    @classmethod
    def get_monthly_stats(cls, db, first_day_ts, last_day_ts):
        stats = cls.get_stats(db, first_day_ts, last_day_ts)
        stats['first_day'] = first_day_ts
        return stats

    def __repr__(self):
        classname = self.__class__.__name__
        return ("<%s()>" % (classname))

