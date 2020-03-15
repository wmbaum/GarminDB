#!/usr/bin/env python3

"""
A script that imports and analyzes Garmin health device data into a database.

The data is either copied from a USB mounted Garmin device or downloaded from Garmin Connect.
"""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"

import logging
import sys
import argparse
import datetime
import os
import tempfile

from version import format_version, python_version_check, log_version
from download_garmin import Download
from copy_garmin import Copy
from import_garmin import GarminProfile, GarminWeightData, GarminSummaryData, GarminMonitoringFitData, GarminSleepData, GarminRhrData, GarminSettingsFitData, GarminHydrationData
from import_garmin_activities import GarminJsonSummaryData, GarminJsonDetailsData, GarminTcxData, GarminActivitiesFitData
from analyze_garmin import Analyze
from export_activities import ActivityExporter

import HealthDB
import GarminDB
import garmin_db_config_manager as GarminDBConfigManager
from garmin_connect_config_manager import GarminConnectConfigManager
from statistics import Statistics
from open_with_basecamp import OpenWithBaseCamp
from open_with_google_earth import OpenWithGoogleEarth


logging.basicConfig(filename='garmin.log', filemode='w', level=logging.INFO)
logger = logging.getLogger(__file__)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))
root_logger = logging.getLogger()

gc_config = GarminConnectConfigManager()


stats_to_db_map = {
    Statistics.monitoring            : GarminDB.MonitoringDB,
    Statistics.steps                 : GarminDB.MonitoringDB,
    Statistics.itime                 : GarminDB.MonitoringDB,
    Statistics.sleep                 : GarminDB.GarminDB,
    Statistics.rhr                   : GarminDB.GarminDB,
    Statistics.weight                : GarminDB.GarminDB,
    Statistics.activities            : GarminDB.ActivitiesDB
}

summary_dbs = [GarminDB.GarminSummaryDB, HealthDB.SummaryDB]


def __get_date_and_days(db, latest, table, col, stat_name):
    if latest:
        last_ts = table.latest_time(db, col)
        if last_ts is None:
            date, days = gc_config.stat_start_date(stat_name)
            logger.info("Recent %s data not found, using: %s : %s", stat_name, date, days)
        else:
            # start from the day before the last day in the DB
            logger.info("Downloading latest %s data from: %s", stat_name, last_ts)
            date = last_ts.date() if isinstance(last_ts, datetime.datetime) else last_ts
            days = max((datetime.date.today() - date).days, 1)
    else:
        date, days = gc_config.stat_start_date(stat_name)
        days = min((datetime.date.today() - date).days, days)
    if date is None or days is None:
        logger.error("Missing config: need %s_start_date and download_days. Edit GarminConnectConfig.py.", stat_name)
        sys.exit()
    return (date, days)


def copy_data(overwite, latest, stats):
    """Copy data from a mounted Garmin USB device to files."""
    logger.info("___Copying Data___")
    copy = Copy(gc_config.device_mount_dir())

    settings_dir = GarminDBConfigManager.get_or_create_fit_files_dir()
    root_logger.info("Copying settings to %s", settings_dir)
    copy.copy_settings(settings_dir)

    if Statistics.activities in stats:
        activities_dir = GarminDBConfigManager.get_or_create_activities_dir()
        root_logger.info("Copying activities to %s", activities_dir)
        copy.copy_activities(activities_dir, latest)

    if Statistics.monitoring in stats:
        monitoring_dir = GarminDBConfigManager.get_or_create_monitoring_dir(datetime.datetime.now().year)
        root_logger.info("Copying monitoring to %s", monitoring_dir)
        copy.copy_monitoring(monitoring_dir, latest)

    if Statistics.sleep in stats:
        monitoring_dir = GarminDBConfigManager.get_or_create_monitoring_dir(datetime.datetime.now().year)
        root_logger.info("Copying sleep to %s", monitoring_dir)
        copy.copy_sleep(monitoring_dir, latest)


def download_data(overwite, latest, stats):
    """Download selected activity types from Garmin Connect and save the data in files. Overwrite previously downloaded data if indicated."""
    logger.info("___Downloading %s Data___", 'Latest' if latest else 'All')
    db_params_dict = GarminDBConfigManager.get_db_params()

    download = Download()
    if not download.login():
        logger.error("Failed to login!")
        sys.exit()

    if Statistics.activities in stats:
        if latest:
            activity_count = gc_config.latest_activity_count()
        else:
            activity_count = gc_config.all_activity_count()
        activities_dir = GarminDBConfigManager.get_or_create_activities_dir()
        root_logger.info("Fetching %d activities to %s", activity_count, activities_dir)
        download.get_activity_types(activities_dir, overwite)
        download.get_activities(activities_dir, activity_count, overwite)
        download.unzip_files(activities_dir)

    if Statistics.monitoring in stats:
        date, days = __get_date_and_days(GarminDB.MonitoringDB(db_params_dict), latest, GarminDB.MonitoringHeartRate, GarminDB.MonitoringHeartRate.heart_rate, 'monitoring')
        if days > 0:
            monitoring_dir = GarminDBConfigManager.get_or_create_monitoring_dir(date.year)
            root_logger.info("Date range to update: %s (%d) to %s", date, days, monitoring_dir)
            download.get_daily_summaries(monitoring_dir, date, days, overwite)
            download.get_hydration(monitoring_dir, date, days, overwite)
            download.get_monitoring(date, days)
            download.unzip_files(monitoring_dir)
            root_logger.info("Saved monitoring files for %s (%d) to %s for processing", date, days, monitoring_dir)

    if Statistics.sleep in stats:
        date, days = __get_date_and_days(GarminDB.GarminDB(db_params_dict), latest, GarminDB.Sleep, GarminDB.Sleep.total_sleep, 'sleep')
        if days > 0:
            sleep_dir = GarminDBConfigManager.get_or_create_sleep_dir()
            root_logger.info("Date range to update: %s (%d) to %s", date, days, sleep_dir)
            download.get_sleep(sleep_dir, date, days, overwite)
            root_logger.info("Saved sleep files for %s (%d) to %s for processing", date, days, sleep_dir)

    if Statistics.weight in stats:
        date, days = __get_date_and_days(GarminDB.GarminDB(db_params_dict), latest, GarminDB.Weight, GarminDB.Weight.weight, 'weight')
        if days > 0:
            weight_dir = GarminDBConfigManager.get_or_create_weight_dir()
            root_logger.info("Date range to update: %s (%d) to %s", date, days, weight_dir)
            download.get_weight(weight_dir, date, days, overwite)
            root_logger.info("Saved weight files for %s (%d) to %s for processing", date, days, weight_dir)

    if Statistics.rhr in stats:
        date, days = __get_date_and_days(GarminDB.GarminDB(db_params_dict), latest, GarminDB.RestingHeartRate, GarminDB.RestingHeartRate.resting_heart_rate, 'rhr')
        if days > 0:
            rhr_dir = GarminDBConfigManager.get_or_create_rhr_dir()
            root_logger.info("Date range to update: %s (%d) to %s", date, days, rhr_dir)
            download.get_rhr(rhr_dir, date, days, overwite)
            root_logger.info("Saved rhr files for %s (%d) to %s for processing", date, days, rhr_dir)


def import_data(debug, latest, stats):
    """Import previously downloaded Garmin data into the database."""
    logger.info("___Importing %s Data___", 'Latest' if latest else 'All')
    db_params_dict = GarminDBConfigManager.get_db_params()

    ignore_dev_fields = gc_config.ignore_dev_fields()

    # Import the user profile and/or settings FIT file first so that we can get the measurement system and some other things sorted out first.
    fit_files_dir = GarminDBConfigManager.get_or_create_fit_files_dir()
    gp = GarminProfile(db_params_dict, fit_files_dir, debug)
    if gp.file_count() > 0:
        gp.process()

    gsfd = GarminSettingsFitData(fit_files_dir, ignore_dev_fields, debug)
    if gsfd.file_count() > 0:
        gsfd.process_files(db_params_dict)

    garmindb = GarminDB.GarminDB(db_params_dict)
    measurement_system = GarminDB.Attributes.measurements_type(garmindb)

    if Statistics.weight in stats:
        weight_dir = GarminDBConfigManager.get_or_create_weight_dir()
        gwd = GarminWeightData(db_params_dict, weight_dir, latest, measurement_system, debug)
        if gwd.file_count() > 0:
            gwd.process()

    if Statistics.monitoring in stats:
        monitoring_dir = GarminDBConfigManager.get_or_create_monitoring_base_dir()
        gsd = GarminSummaryData(db_params_dict, monitoring_dir, latest, measurement_system, debug)
        if gsd.file_count() > 0:
            gsd.process()

        ghd = GarminHydrationData(db_params_dict, monitoring_dir, latest, measurement_system, debug)
        if ghd.file_count() > 0:
            ghd.process()

        gfd = GarminMonitoringFitData(monitoring_dir, latest, measurement_system, ignore_dev_fields, debug)
        if gfd.file_count() > 0:
            gfd.process_files(db_params_dict)

    if Statistics.sleep in stats:
        sleep_dir = GarminDBConfigManager.get_or_create_sleep_dir()
        gsd = GarminSleepData(db_params_dict, sleep_dir, latest, debug)
        if gsd.file_count() > 0:
            gsd.process()

    if Statistics.rhr in stats:
        rhr_dir = GarminDBConfigManager.get_or_create_rhr_dir()
        grhrd = GarminRhrData(db_params_dict, rhr_dir, latest, debug)
        if grhrd.file_count() > 0:
            grhrd.process()

    if Statistics.activities in stats:
        activities_dir = GarminDBConfigManager.get_or_create_activities_dir()
        # Tcx fields are less precise than the JSON files, so load Tcx first and overwrite with better JSON values.
        gtd = GarminTcxData(activities_dir, latest, measurement_system, debug)
        if gtd.file_count() > 0:
            gtd.process_files(db_params_dict)

        gjsd = GarminJsonSummaryData(db_params_dict, activities_dir, latest, measurement_system, debug)
        if gjsd.file_count() > 0:
            gjsd.process()

        gdjd = GarminJsonDetailsData(db_params_dict, activities_dir, latest, measurement_system, debug)
        if gdjd.file_count() > 0:
            gdjd.process()

        gfd = GarminActivitiesFitData(activities_dir, latest, measurement_system, ignore_dev_fields, debug)
        if gfd.file_count() > 0:
            gfd.process_files(db_params_dict)


def analyze_data(debug):
    """Analyze the downloaded and imported Garmin data and create summary tables."""
    logger.info("___Analyzing Data___")
    db_params_dict = GarminDBConfigManager.get_db_params()
    analyze = Analyze(db_params_dict, debug - 1)
    analyze.get_stats()
    analyze.summary()
    analyze.create_dynamic_views()


def delete_dbs(delete_db_list=[]):
    """Delete selected, or all if none selected GarminDB, database files."""
    db_params_dict = GarminDBConfigManager.get_db_params()
    if len(delete_db_list) == 0:
        delete_db_list = [GarminDB.GarminDB, GarminDB.MonitoringDB, GarminDB.ActivitiesDB, GarminDB.GarminSummaryDB, HealthDB.SummaryDB]
    for db in delete_db_list:
        db.delete_db(db_params_dict)


def export_activity(debug, directory, export_activity_id):
    """Export an activity given its database id."""
    db_params_dict = GarminDBConfigManager.get_db_params()
    garmindb = GarminDB.GarminDB(db_params_dict)
    measurement_system = GarminDB.Attributes.measurements_type(garmindb)
    ae = ActivityExporter(directory, export_activity_id, measurement_system, debug)
    ae.process(db_params_dict)
    return ae.write('activity_%s.tcx' % export_activity_id)


def basecamp_activity(debug, export_activity_id):
    """Export an activity given its database id."""
    file_with_path = export_activity(debug, tempfile.mkdtemp(), export_activity_id)
    logger.info("Opening activity %d (%s) in BaseCamp", export_activity_id, file_with_path)
    OpenWithBaseCamp.open(file_with_path)


def google_earth_activity(debug, export_activity_id):
    """Export an activity given its database id."""
    file_with_path = export_activity(debug, tempfile.mkdtemp(), export_activity_id)
    logger.info("Opening activity %d (%s) in GoogleEarth", export_activity_id, file_with_path)
    OpenWithGoogleEarth.open(file_with_path)


def main(argv):
    """Manage Garmin device data."""
    python_version_check(sys.argv[0])

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version", help="print the program's version", action='version', version=format_version(sys.argv[0]))
    parser.add_argument("-t", "--trace", help="Turn on debug tracing", type=int, default=0)
    modes_group = parser.add_argument_group('Modes')
    modes_group.add_argument("-d", "--download", help="Download data from Garmin Connect for the chosen stats.", dest='download_data', action="store_true", default=False)
    modes_group.add_argument("-c", "--copy", help="copy data from a connected device", dest='copy_data', action="store_true", default=False)
    modes_group.add_argument("-i", "--import", help="Import data for the chosen stats", dest='import_data', action="store_true", default=False)
    modes_group.add_argument("--analyze", help="Analyze data in the db and create summary and derived tables.", dest='analyze_data', action="store_true", default=False)
    modes_group.add_argument("--delete_db", help="Delete Garmin DB db files for the selected activities.", action="store_true", default=False)
    modes_group.add_argument("-e", "--export-activity", help="Export an activity to a TCX file based on the activity\'s id", type=int)
    modes_group.add_argument("-b", "--basecamp-activity", help="Export an activity to Garmin BaseCamp", type=int)
    modes_group.add_argument("-g", "--google-earth-activity", help="Export an activity to Google Earth", type=int)
    # stat types to operate on
    stats_group = parser.add_argument_group('Statistics')
    stats_group.add_argument("-A", "--all", help="Download and/or import data for all enabled stats.", action='store_const', dest='stats',
                             const=gc_config.enabled_stats(), default=[])
    stats_group.add_argument("-a", "--activities", help="Download and/or import activities data.", dest='stats', action='append_const', const=Statistics.activities)
    stats_group.add_argument("-m", "--monitoring", help="Download and/or import monitoring data.", dest='stats', action='append_const', const=Statistics.monitoring)
    stats_group.add_argument("-r", "--rhr", help="Download and/or import resting heart rate data.", dest='stats', action='append_const', const=Statistics.rhr)
    stats_group.add_argument("-s", "--sleep", help="Download and/or import sleep data.", dest='stats', action='append_const', const=Statistics.sleep)
    stats_group.add_argument("-w", "--weight", help="Download and/or import weight data.", dest='stats', action='append_const', const=Statistics.weight)
    modifiers_group = parser.add_argument_group('Modifiers')
    modifiers_group.add_argument("-l", "--latest", help="Only download and/or import the latest data.", action="store_true", default=False)
    modifiers_group.add_argument("-o", "--overwrite", help="Overwite existing files when downloading. The default is to only download missing files.",
                                 action="store_true", default=False)
    args = parser.parse_args()

    log_version(sys.argv[0])

    if args.trace > 0:
        root_logger.setLevel(logging.DEBUG)
    else:
        root_logger.setLevel(logging.INFO)

    root_logger.info("Enabled statistics: %r", args.stats)

    if args.delete_db:
        delete_dbs([stats_to_db_map[stat] for stat in args.stats] + summary_dbs)
        sys.exit()

    if args.copy_data:
        copy_data(args.overwrite, args.latest, args.stats)

    if args.download_data:
        download_data(args.overwrite, args.latest, args.stats)

    if args.import_data:
        import_data(args.trace, args.latest, args.stats)

    if args.analyze_data:
        analyze_data(args.trace)

    if args.export_activity:
        export_activity(args.trace, os.getcwd(), args.export_activity)

    if args.basecamp_activity:
        basecamp_activity(args.trace, args.basecamp_activity)

    if args.google_earth_activity:
        google_earth_activity(args.trace, args.google_earth_activity)


if __name__ == "__main__":
    main(sys.argv[1:])
