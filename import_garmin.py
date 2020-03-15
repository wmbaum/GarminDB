"""Objects for importing Garmin data from Garmin Connect downloads and FIT files."""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"


import sys
import logging
import datetime
import enum
import dateutil.parser

import Fit
import GarminDB
from utilities import JsonFileProcessor
from fit_data import FitData


logger = logging.getLogger(__file__)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))
root_logger = logging.getLogger()


class GarminWeightData(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect weight data into a database."""

    def __init__(self, db_params, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminWeightData.

        Parameters:
        ----------
        db_params (object): configuration data for accessing the database
        input_dir (string): directory (full path) to check for weight data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing weight data")
        super().__init__(r'weight_\d{4}-\d{2}-\d{2}\.json', input_dir=input_dir, latest=latest, debug=debug)
        self.measurement_system = measurement_system
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {'startDate': dateutil.parser.parse}

    def _process_json(self, json_data):
        weight_list = json_data['dateWeightList']
        if len(weight_list) > 0:
            weight = Fit.Weight.from_grams(weight_list[0]['weight'])
            point = {
                'day'       : json_data['startDate'].date(),
                'weight'    : weight.kgs_or_lbs(self.measurement_system)
            }
            GarminDB.Weight.insert_or_update(self.garmin_db, point)
            return 1
        return 0


class GarminMonitoringFitData(FitData):
    """Class for importing monitoring FIT files into a database."""

    def __init__(self, input_dir, latest, measurement_system, ignore_dev_fields, debug):
        """
        Return an instance of GarminMonitoringFitData.

        Parameters:
        ----------
        input_dir (string): directory (full path) to check for monitoring data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        ignore_dev_fields (Boolean): if True, then ignore developer fields in Fit file
        debug (Boolean): enable debug logging

        """
        super().__init__(input_dir, ignore_dev_fields, debug, latest, True, [Fit.FileType.monitoring_b], measurement_system)


class GarminSettingsFitData(FitData):
    """Class for importing settings FIT files into a database."""

    def __init__(self, input_dir, ignore_dev_fields, debug):
        """
        Return an instance of GarminSettingsFitData.

        Parameters:
        ----------
        input_dir (string): directory (full path) to check for settings data files
        ignore_dev_fields (Boolean): if True, then ignore developer fields in Fit file
        debug (Boolean): enable debug logging

        """
        super().__init__(input_dir, ignore_dev_fields, debug, fit_types=[Fit.FileType.settings])


class SleepActivityLevels(enum.Enum):
    """Enum of values used to encode activity levels during sleep on Gamin Connect."""

    deep_sleep = 0.0
    light_sleep = 1.0
    awake = 2.0
    more_awake = 3.0


class RemSleepActivityLevels(enum.Enum):
    """Enum of values used to encode activity levels during sleep (including REM sleep) on Gamin Connect."""

    unmeasurable = -1.0
    deep_sleep = 0.0
    light_sleep = 1.0
    rem_sleep = 2.0
    awake = 3.0


class GarminSleepData(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect sleep data into a database."""

    def __init__(self, db_params, input_dir, latest, debug):
        """
        Return an instance of GarminSleepData.

        Parameters:
        ----------
        db_params (object): configuration data for accessing the database
        input_dir (string): directory (full path) to check for sleep data files
        latest (Boolean): check for latest files only
        debug (Boolean): enable debug logging

        """
        logger.info("Processing sleep data")
        super().__init__(r'sleep_\d{4}-\d{2}-\d{2}\.json', input_dir=input_dir, latest=latest, debug=debug)
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {
            'calendarDate'              : dateutil.parser.parse,
            'sleepTimeSeconds'          : Fit.conversions.secs_to_dt_time,
            'sleepStartTimestampGMT'    : Fit.conversions.epoch_ms_to_dt,
            'sleepEndTimestampGMT'      : Fit.conversions.epoch_ms_to_dt,
            'deepSleepSeconds'          : Fit.conversions.secs_to_dt_time,
            'lightSleepSeconds'         : Fit.conversions.secs_to_dt_time,
            'remSleepSeconds'           : Fit.conversions.secs_to_dt_time,
            'awakeSleepSeconds'         : Fit.conversions.secs_to_dt_time,
            'startGMT'                  : dateutil.parser.parse,
            'endGMT'                    : dateutil.parser.parse
        }

    def _process_json(self, json_data):
        daily_sleep = json_data.get('dailySleepDTO')
        if daily_sleep is None:
            return 0
        date = daily_sleep.get('calendarDate')
        if date is None:
            return 0
        day = date.date()
        if json_data.get('remSleepData'):
            root_logger.info("Importing %s with REM data", day)
            sleep_activity_levels = RemSleepActivityLevels
        else:
            root_logger.info("Importing %s without REM data", day)
            sleep_activity_levels = SleepActivityLevels
        day_data = {
            'day' : day,
            'start': daily_sleep.get('sleepStartTimestampGMT'),
            'end': daily_sleep.get('sleepEndTimestampGMT'),
            'total_sleep': daily_sleep.get('sleepTimeSeconds'),
            'deep_sleep': daily_sleep.get('deepSleepSeconds'),
            'light_sleep': daily_sleep.get('lightSleepSeconds'),
            'rem_sleep': daily_sleep.get('remSleepSeconds'),
            'awake': daily_sleep.get('awakeSleepSeconds')
        }
        GarminDB.Sleep.insert_or_update(self.garmin_db, day_data, ignore_none=True)
        sleep_levels = json_data.get('sleepLevels')
        if sleep_levels is None:
            return 0
        for sleep_level in sleep_levels:
            start = sleep_level['startGMT']
            end = sleep_level['endGMT']
            event = sleep_activity_levels(sleep_level['activityLevel'])
            duration = (datetime.datetime.min + (end - start)).time()
            level_data = {
                'timestamp': start,
                'event': event.name,
                'duration': duration
            }
            GarminDB.SleepEvents.insert_or_update(self.garmin_db, level_data, ignore_none=True)
        return len(sleep_levels)


class GarminRhrData(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect resting heart rate data into a database."""

    def __init__(self, db_params, input_dir, latest, debug):
        """
        Return an instance of GarminRhrData.

        Parameters:
        ----------
        db_params (object): configuration data for accessing the database
        input_dir (string): directory (full path) to check for resting heart rate data files
        latest (Boolean): check for latest files only
        debug (Boolean): enable debug logging

        """
        logger.info("Processing rhr data")
        super().__init__(r'rhr_\d{4}-\d{2}-\d{2}\.json', input_dir=input_dir, latest=latest, debug=debug)
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {'statisticsStartDate': dateutil.parser.parse}

    def _process_json(self, json_data):
        rhr_list = json_data['allMetrics']['metricsMap']['WELLNESS_RESTING_HEART_RATE']
        if len(rhr_list) > 0:
            rhr = rhr_list[0].get('value')
            if rhr:
                point = {
                    'day'                   : json_data['statisticsStartDate'].date(),
                    'resting_heart_rate'    : rhr
                }
                GarminDB.RestingHeartRate.insert_or_update(self.garmin_db, point, ignore_none=True)
                return 1
        return 0


class GarminProfile(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect profile data into a database."""

    def __init__(self, db_params, input_dir, debug):
        """
        Return an instance of GarminProfile.

        Parameters:
        ----------
        db_params (object): configuration data for accessing the database
        input_dir (string): directory (full path) to check for profile data files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing profile data")
        super().__init__(r'profile\.json', input_dir=input_dir, latest=False, debug=debug)
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {'calendarDate' : dateutil.parser.parse}

    def _process_json(self, json_data):
        measurement_system = Fit.field_enums.DisplayMeasure.from_string(json_data['measurementSystem'])
        attributes = {
            'name'                  : json_data['displayName'].replace('_', ' '),
            'time_zone'             : json_data['timeZone'],
            'measurement_system'    : str(measurement_system),
            'date_format'           : json_data['dateFormat']['formatKey']
        }
        for attribute_name, attribute_value in attributes.items():
            GarminDB.Attributes.set_newer(self.garmin_db, attribute_name, attribute_value)
        return len(attributes)


class GarminSummaryData(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect daily summary data into a database."""

    def __init__(self, db_params, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminSummaryData.

        Parameters:
        ----------
        db_params (dict): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing daily summary data")
        super().__init__(r'daily_summary_\d{4}-\d{2}-\d{2}\.json', input_dir=input_dir, latest=latest, debug=debug, recursive=True)
        self.input_dir = input_dir
        self.measurement_system = measurement_system
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {
            'calendarDate'              : dateutil.parser.parse,
            'moderateIntensityMinutes'  : Fit.conversions.min_to_dt_time,
            'vigorousIntensityMinutes'  : Fit.conversions.min_to_dt_time,
            'intensityMinutesGoal'      : Fit.conversions.min_to_dt_time,
        }

    def _process_json(self, json_data):
        day = json_data['calendarDate'].date()
        distance = Fit.Distance.from_meters(self._get_field(json_data, 'totalDistanceMeters', int))
        summary = {
            'day'                       : day,
            'hr_min'                    : self._get_field(json_data, 'minHeartRate', float),
            'hr_max'                    : self._get_field(json_data, 'maxHeartRate', float),
            'rhr'                       : self._get_field(json_data, 'restingHeartRate', float),
            'stress_avg'                : self._get_field(json_data, 'averageStressLevel', float),
            'step_goal'                 : self._get_field(json_data, 'dailyStepGoal', int),
            'steps'                     : self._get_field(json_data, 'totalSteps', int),
            'floors_goal'               : self._get_field(json_data, 'userFloorsAscendedGoal', float),
            'moderate_activity_time'    : json_data.get('moderateIntensityMinutes'),
            'vigorous_activity_time'    : json_data.get('vigorousIntensityMinutes'),
            'intensity_time_goal'       : json_data.get('intensityMinutesGoal'),
            'floors_up'                 : self._get_field(json_data, 'floorsAscended', float),
            'floors_down'               : self._get_field(json_data, 'floorsDescended', float),
            'distance'                  : distance.kms_or_miles(self.measurement_system),
            'calories_goal'             : self._get_field(json_data, 'netCalorieGoal', float),
            'calories_total'            : self._get_field(json_data, 'totalKilocalories', float),
            'calories_bmr'              : self._get_field(json_data, 'bmrKilocalories', float),
            'calories_active'           : self._get_field(json_data, 'activeKilocalories', float),
            'calories_consumed'         : self._get_field(json_data, 'consumedKilocalories', float),
            'spo2_avg'                  : self._get_field(json_data, 'averageSpo2', float),
            'spo2_min'                  : self._get_field(json_data, 'lowestSpo2', float),
            'rr_waking_avg'             : self._get_field(json_data, 'avgWakingRespirationValue', float),
            'rr_max'                    : self._get_field(json_data, 'highestRespirationValue', float),
            'rr_min'                    : self._get_field(json_data, 'lowestRespirationValue', float),
            'description'               : self._get_field(json_data, 'wellnessDescription'),
        }
        GarminDB.DailySummary.insert_or_update(self.garmin_db, summary, ignore_none=True)
        return 1


class GarminHydrationData(JsonFileProcessor):
    """Class for importing JSON formatted Garmin Connect daily summary data into a database."""

    def __init__(self, db_params, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminHydrationData.

        Parameters:
        ----------
        db_params (object): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing daily hydration data")
        super().__init__(r'hydration_\d{4}-\d{2}-\d{2}\.json', input_dir=input_dir, latest=latest, debug=debug, recursive=True)
        self.input_dir = input_dir
        self.measurement_system = measurement_system
        self.garmin_db = GarminDB.GarminDB(db_params)
        self.conversions = {
            'calendarDate': dateutil.parser.parse
        }

    def _process_json(self, json_data):
        hydration_intake = Fit.Volume.from_milliliters(json_data['valueInML'])
        hydration_goal = Fit.Volume.from_milliliters(json_data['baseGoalInML'])
        sweat_loss = Fit.Volume.from_milliliters(json_data['sweatLossInML'])
        summary = {
            'day'                       : json_data['calendarDate'].date(),
            'hydration_intake'          : hydration_intake.ml_or_oz(self.measurement_system, rounded=True),
            'hydration_goal'            : hydration_goal.ml_or_oz(self.measurement_system, rounded=True),
            'sweat_loss'                : sweat_loss.ml_or_oz(self.measurement_system, rounded=True)
        }
        root_logger.info("Processing daily hydration data %r", summary)
        GarminDB.DailySummary.insert_or_update(self.garmin_db, summary, ignore_none=True)
        return 1
