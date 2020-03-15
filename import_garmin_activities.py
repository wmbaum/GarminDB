"""Objects for importing Garmin activity data from Garmin Connect downloads and FIT files."""

__author__ = "Tom Goetz"
__copyright__ = "Copyright Tom Goetz"
__license__ = "GPL"


import sys
import logging
from tqdm import tqdm
import dateutil.parser

import Fit
import GarminDB
from utilities import FileProcessor, JsonFileProcessor
import garmin_connect_enums as GarminConnectEnums
from garmin_db_tcx import GarminDbTcx
from fit_data import FitData


logger = logging.getLogger(__file__)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))
root_logger = logging.getLogger()


class GarminActivitiesFitData(FitData):
    """Class for importing Garmin activity data from FIT files."""

    def __init__(self, input_dir, latest, measurement_system, ignore_dev_fields, debug):
        """
        Return an instance of GarminActivitiesFitData.

        Parameters:
        ----------
        db_params (dict): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        ignore_dev_fields (Boolean): if True, then ignore developer fields in Fit file
        debug (Boolean): enable debug logging

        """
        super().__init__(input_dir, ignore_dev_fields, debug, latest, False, [Fit.FileType.activity], measurement_system)


class GarminTcxData(object):
    """Class for importing Garmin activity data from TCX files."""

    def __init__(self, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminTcxData.

        Parameters:
        ----------
        db_params (dict): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing activities tcx data")
        self.measurement_system = measurement_system
        self.debug = debug
        if input_dir:
            self.file_names = FileProcessor.dir_to_files(input_dir, GarminDbTcx.filename_regex, latest)

    def file_count(self):
        """Return the number of files that will be propcessed."""
        return len(self.file_names)

    def __process_record(self, tcx, activity_id, record_number, point):
        root_logger.debug("Processing record: %r (%d)", point, record_number)
        if not GarminDB.ActivityRecords.s_exists(self.garmin_act_db_session, {'activity_id' : activity_id, 'record' : record_number}):
            record = {
                'activity_id'                       : activity_id,
                'record'                            : record_number,
                'timestamp'                         : tcx.get_point_time(point),
                'hr'                                : tcx.get_point_hr(point),
                'altitude'                          : tcx.get_point_altitude(point).meters_or_feet(measurement_system=self.measurement_system),
                'speed'                             : tcx.get_point_speed(point).kph_or_mph(measurement_system=self.measurement_system)
            }
            loc = tcx.get_point_loc(point)
            if loc is not None:
                record.update({'position_lat': loc.lat_deg, 'position_long': loc.long_deg})
            self.garmin_act_db_session.add(GarminDB.ActivityRecords(**record))

    def __process_lap(self, tcx, activity_id, lap_number, lap):
        root_logger.info("Processing lap: %d", lap_number)
        for record_number, point in enumerate(tcx.get_lap_points(lap)):
            self.__process_record(tcx, activity_id, record_number, point)
        if not GarminDB.ActivityLaps.s_exists(self.garmin_act_db_session, {'activity_id' : activity_id, 'lap' : lap_number}):
            lap_data = {
                'activity_id'                       : activity_id,
                'lap'                               : lap_number,
                'start_time'                        : tcx.get_lap_start(lap),
                'stop_time'                         : tcx.get_lap_end(lap),
                'elapsed_time'                      : tcx.get_lap_duration(lap),
                'distance'                          : tcx.get_lap_distance(lap).meters_or_feet(measurement_system=self.measurement_system),
                'calories'                          : tcx.get_lap_calories(lap)
            }
            start_loc = tcx.get_lap_start_loc(lap)
            if start_loc is not None:
                lap_data.update({'start_lat': start_loc.lat_deg, 'start_long': start_loc.long_deg})
            end_loc = tcx.get_lap_end_loc(lap)
            if end_loc is not None:
                lap_data.update({'stop_lat': end_loc.lat_deg, 'stop_long': end_loc.long_deg})
            root_logger.info("Inserting lap: %r (%d): %r", lap, lap_number, lap_data)
            self.garmin_act_db_session.add(GarminDB.ActivityLaps(**lap_data))

    def __process_file(self, file_name):
        tcx = GarminDbTcx()
        tcx.read(file_name)
        start_time = tcx.start_time
        (manufacturer, product) = tcx.get_manufacturer_and_product()
        serial_number = tcx.serial_number
        device = {
            'serial_number'     : serial_number,
            'timestamp'         : start_time,
            'manufacturer'      : manufacturer,
            'product'           : product,
            'hardware_version'  : None,
        }
        GarminDB.Device.s_insert_or_update(self.garmin_db_session, device, ignore_none=True)
        root_logger.info("Processing file: %s for manufacturer %s product %s device %s", file_name, manufacturer, product, serial_number)
        (file_id, file_name) = GarminDB.File.name_and_id_from_path(file_name)
        file = {
            'id'            : file_id,
            'name'          : file_name,
            'type'          : GarminDB.File.FileType.tcx,
            'serial_number' : serial_number,
        }
        GarminDB.File.s_insert_or_update(self.garmin_db_session, file)
        activity = {
            'activity_id'               : file_id,
            'start_time'                : start_time,
            'stop_time'                 : tcx.end_time,
            'laps'                      : tcx.lap_count,
            'sport'                     : tcx.sport,
            'calories'                  : tcx.calories,
            'distance'                  : tcx.distance.kms_or_miles(self.measurement_system),
            'avg_hr'                    : tcx.hr_avg,
            'max_hr'                    : tcx.hr_max,
            'max_cadence'               : tcx.cadence_max,
            'avg_cadence'               : tcx.cadence_avg,
            'ascent'                    : tcx.ascent.meters_or_feet(self.measurement_system),
            'descent'                   : tcx.descent.meters_or_feet(self.measurement_system)
        }
        start_loc = tcx.start_loc
        if start_loc is not None:
            activity.update({'start_lat': start_loc.lat_deg, 'start_long': start_loc.long_deg})
        end_loc = tcx.end_loc
        if end_loc is not None:
            activity.update({'stop_lat': end_loc.lat_deg, 'stop_long': end_loc.long_deg})
        GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True, ignore_zero=True)
        for lap_number, lap in enumerate(tcx.laps):
            self.__process_lap(tcx, file_id, lap_number, lap)

    def process_files(self, db_params):
        """Import data from TCX files into the database."""
        garmin_db = GarminDB.GarminDB(db_params, self.debug - 1)
        garmin_act_db = GarminDB.ActivitiesDB(db_params, self.debug - 1)
        with garmin_db.managed_session() as self.garmin_db_session, garmin_act_db.managed_session() as self.garmin_act_db_session:
            for file_name in tqdm(self.file_names, unit='files'):
                try:
                    self.__process_file(file_name)
                except Exception as e:
                    logger.error('Failed to processes file %s: %s', file_name, e)
                self.garmin_db_session.commit()
                self.garmin_act_db_session.commit()


class GarminJsonSummaryData(JsonFileProcessor):
    """Class for importing Garmin activity data from JSON formatted Garmin Connect summary downloads."""

    def __init__(self, db_params, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminTcxData.

        Parameters:
        ----------
        db_params (dict): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing %s activities summary data from %s", 'latest' if latest else 'all', input_dir)
        super().__init__(r'activity_\d*\.json', input_dir=input_dir, latest=latest, debug=debug)
        self.input_dir = input_dir
        self.measurement_system = measurement_system
        self.garmin_act_db = GarminDB.ActivitiesDB(db_params, self.debug - 1)
        self.conversions = {}

    def _commit(self):
        self.garmin_act_db_session.commit()

    def _process_steps_activity(self, activity_id, activity_summary):
        root_logger.debug("process_steps_activity for %s", activity_id)
        avg_vertical_oscillation = self._get_field_obj(activity_summary, 'avgVerticalOscillation', Fit.Distance.from_meters)
        avg_step_length = self._get_field_obj(activity_summary, 'avgStrideLength', Fit.Distance.from_meters)
        run = {
            'activity_id'               : activity_id,
            'steps'                     : self._get_field(activity_summary, 'steps', float),
            'avg_steps_per_min'         : self._get_field(activity_summary, 'averageRunningCadenceInStepsPerMinute', float),
            'max_steps_per_min'         : self._get_field(activity_summary, 'maxRunningCadenceInStepsPerMinute', float),
            'avg_step_length'           : avg_step_length.meters_or_feet(self.measurement_system),
            'avg_gct_balance'           : self._get_field(activity_summary, 'avgGroundContactBalance', float),
            'avg_vertical_oscillation'  : avg_vertical_oscillation.meters_or_feet(self.measurement_system),
            'avg_ground_contact_time'   : Fit.conversions.ms_to_dt_time(self._get_field(activity_summary, 'avgGroundContactTime', float)),
            'vo2_max'                   : self._get_field(activity_summary, 'vO2MaxValue', float),
        }
        GarminDB.StepsActivities.s_insert_or_update(self.garmin_act_db_session, run, ignore_none=True)

    def _process_inline_skating(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("inline_skating for %s: %r", activity_id, activity_summary)

    def _process_snowshoeing(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("snow_shoe for %s: %r", activity_id, activity_summary)

    def _process_strength_training(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("strength_training for %s: %r", activity_id, activity_summary)

    def _process_stand_up_paddleboarding(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("stand_up_paddleboarding for %s: %r", activity_id, activity_summary)

    def _process_resort_skiing_snowboarding(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("resort_skiing_snowboarding for %s: %r", activity_id, activity_summary)

    def _process_running(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("process_running for %s", activity_id)
        self._process_steps_activity(activity_id, activity_summary)
    #
    # def _process_treadmill_running(self, sub_sport, activity_id, activity_summary):
    #     root_logger.debug("process_treadmill_running for %s", activity_id)
    #     self._process_steps_activity(activity_id, activity_summary)

    def _process_walking(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("process_walking for %s", activity_id)
        self._process_steps_activity(activity_id, activity_summary)

    def _process_hiking(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("process_hiking for %s", activity_id)
        self._process_steps_activity(activity_id, activity_summary)

    def _process_paddling(self, sub_sport, activity_id, activity_summary):
        activity = {
            'activity_id'               : activity_id,
            'avg_cadence'               : self._get_field(activity_summary, 'avgStrokeCadence', float),
            'max_cadence'               : self._get_field(activity_summary, 'maxStrokeCadence', float),
        }
        GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True)
        avg_stroke_distance = Fit.Distance.from_meters(self._get_field(activity_summary, 'avgStrokeDistance', float))
        paddle = {
            'activity_id'               : activity_id,
            'strokes'                   : self._get_field(activity_summary, 'strokes', float),
            'avg_stroke_distance'       : avg_stroke_distance.meters_or_feet(self.measurement_system),
        }
        GarminDB.PaddleActivities.s_insert_or_update(self.garmin_act_db_session, paddle, ignore_none=True)

    def _process_cycling(self, sub_sport, activity_id, activity_summary):
        activity = {
            'activity_id'               : activity_id,
            'avg_cadence'               : self._get_field(activity_summary, 'averageBikingCadenceInRevPerMinute', float),
            'max_cadence'               : self._get_field(activity_summary, 'maxBikingCadenceInRevPerMinute', float),
        }
        GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True)
        ride = {
            'activity_id'               : activity_id,
            'strokes'                   : self._get_field(activity_summary, 'strokes', float),
            'vo2_max'                   : self._get_field(activity_summary, 'vO2MaxValue', float),
        }
        GarminDB.CycleActivities.s_insert_or_update(self.garmin_act_db_session, ride, ignore_none=True)

    def _process_mountain_biking(self, activity_id, activity_summary):
        return self._process_cycling(activity_id, activity_summary)

    def _process_elliptical(self, sub_sport, activity_id, activity_summary):
        if activity_summary is not None:
            activity = {
                'activity_id'               : activity_id,
                'avg_cadence'               : self._get_field(activity_summary, 'averageRunningCadenceInStepsPerMinute', float),
                'max_cadence'               : self._get_field(activity_summary, 'maxRunningCadenceInStepsPerMinute', float),
            }
            GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True)
            workout = {
                'activity_id'               : activity_id,
                'steps'                     : self._get_field(activity_summary, 'steps', float),
            }
            GarminDB.EllipticalActivities.s_insert_or_update(self.garmin_act_db_session, workout, ignore_none=True)

    def _process_fitness_equipment(self, sub_sport, activity_id, activity_summary):
        root_logger.debug("process_fitness_equipment (%s) for %s", sub_sport, activity_id)
        self._call_process_func(sub_sport.name, None, activity_id, activity_summary)

    def _process_json(self, json_data):
        activity_id = json_data['activityId']
        distance = self._get_field_obj(json_data, 'distance', Fit.Distance.from_meters)
        ascent = self._get_field_obj(json_data, 'elevationGain', Fit.Distance.from_meters)
        descent = self._get_field_obj(json_data, 'elevationLoss', Fit.Distance.from_meters)
        avg_speed = self._get_field_obj(json_data, 'averageSpeed', Fit.Speed.from_mps)
        max_speed = self._get_field_obj(json_data, 'maxSpeed', Fit.Speed.from_mps)
        max_temperature = self._get_field_obj(json_data, 'maxTemperature', Fit.Temperature.from_celsius)
        min_temperature = self._get_field_obj(json_data, 'minTemperature', Fit.Temperature.from_celsius)
        event = GarminConnectEnums.Event.from_json(json_data)
        sport, sub_sport = GarminConnectEnums.get_summary_sport(json_data)
        activity = {
            'activity_id'               : activity_id,
            'name'                      : json_data.get('activityName'),
            'description'               : self._get_field(json_data, 'description'),
            'type'                      : event.name,
            'sport'                     : sport.name,
            'sub_sport'                 : sub_sport.name,
            'start_time'                : dateutil.parser.parse(self._get_field(json_data, 'startTimeLocal'), ignoretz=True),
            'elapsed_time'              : Fit.conversions.secs_to_dt_time(self._get_field(json_data, 'elapsedDuration', int)),
            'moving_time'               : Fit.conversions.secs_to_dt_time(self._get_field(json_data, 'movingDuration', int)),
            'start_lat'                 : self._get_field(json_data, 'startLatitude', float),
            'start_long'                : self._get_field(json_data, 'startLongitude', float),
            'stop_lat'                  : self._get_field(json_data, 'endLatitude', float),
            'stop_long'                 : self._get_field(json_data, 'endLongitude', float),
            'distance'                  : distance.kms_or_miles(self.measurement_system),
            'laps'                      : self._get_field(json_data, 'lapCount'),
            'avg_hr'                    : self._get_field(json_data, 'averageHR', float),
            'max_hr'                    : self._get_field(json_data, 'maxHR', float),
            'calories'                  : self._get_field(json_data, 'calories', float),
            'avg_speed'                 : avg_speed.kph_or_mph(self.measurement_system),
            'max_speed'                 : max_speed.kph_or_mph(self.measurement_system),
            'ascent'                    : ascent.meters_or_feet(self.measurement_system),
            'descent'                   : descent.meters_or_feet(self.measurement_system),
            'max_temperature'           : max_temperature.c_or_f(self.measurement_system),
            'min_temperature'           : min_temperature.c_or_f(self.measurement_system),
            'training_effect'           : self._get_field(json_data, 'aerobicTrainingEffect', float),
            'anaerobic_training_effect' : self._get_field(json_data, 'anaerobicTrainingEffect', float),
        }
        GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True)
        self._call_process_func(sport.name, sub_sport, activity_id, json_data)
        return 1

    def process(self):
        """Import data from files into the database."""
        with self.garmin_act_db.managed_session() as self.garmin_act_db_session:
            self._process_files()


class GarminJsonDetailsData(JsonFileProcessor):
    """Class for importing Garmin activity data from JSON formatted Garmin Connect details downloads."""

    def __init__(self, db_params, input_dir, latest, measurement_system, debug):
        """
        Return an instance of GarminJsonDetailsData.

        Parameters:
        ----------
        db_params (dict): configuration data for accessing the database
        input_dir (string): directory (full path) to check for data files
        latest (Boolean): check for latest files only
        measurement_system (enum): which measurement system to use when importing the files
        debug (Boolean): enable debug logging

        """
        logger.info("Processing activities detail data")
        super().__init__(r'activity_details_\d*\.json', input_dir=input_dir, latest=latest, debug=debug)
        self.measurement_system = measurement_system
        self.garmin_act_db = GarminDB.ActivitiesDB(db_params, self.debug - 1)
        self.conversions = {}

    def _commit(self):
        self.garmin_act_db_session.commit()

    def _process_steps_activity(self, sub_sport, activity_id, json_data):
        summary_dto = json_data['summaryDTO']
        avg_moving_speed_mps = summary_dto.get('averageMovingSpeed')
        avg_moving_speed = Fit.conversions.mps_to_mph(avg_moving_speed_mps)
        run = {
            'activity_id'       : activity_id,
            'avg_moving_pace'   : Fit.conversions.perhour_speed_to_pace(avg_moving_speed),
        }
        root_logger.debug("steps_activity for %d: %r", activity_id, run)
        GarminDB.StepsActivities.s_insert_or_update(self.garmin_act_db_session, run, ignore_none=True)

    def _process_cycling(self, sub_sport, activity_id, json_data):
        root_logger.debug("cycling (%s) for %d: %r", sub_sport, activity_id, json_data)

    def _process_elliptical(self, sub_sport, activity_id, json_data):
        root_logger.debug("elliptical for %d: %r", activity_id, json_data)

    def _process_hiking(self, sub_sport, activity_id, json_data):
        root_logger.debug("hiking for %d: %r", activity_id, json_data)
        self._process_steps_activity(sub_sport, activity_id, json_data)

    def _process_inline_skating(self, sub_sport, activity_id, json_data):
        root_logger.debug("inline_skating for %d: %r", activity_id, json_data)

    def _process_paddling(self, sub_sport, activity_id, json_data):
        root_logger.debug("paddling for %d: %r", activity_id, json_data)
    #
    # def _process_mountain_biking(self, sub_sport, activity_id, json_data):
    #     root_logger.debug("mountain_biking for %d: %r", activity_id, json_data)

    def _process_resort_skiing_snowboarding(self, sub_sport, activity_id, json_data):
        root_logger.debug("resort_skiing_snowboarding for %d: %r", activity_id, json_data)

    def _process_snowshoeing(self, sub_sport, activity_id, json_data):
        root_logger.debug("snow_shoe for %d: %r", activity_id, json_data)

    def _process_strength_training(self, sub_sport, activity_id, json_data):
        root_logger.debug("strength_training for %d: %r", activity_id, json_data)

    def _process_stand_up_paddleboarding(self, sub_sport, activity_id, json_data):
        root_logger.debug("stand_up_paddleboarding for %d: %r", activity_id, json_data)
    #
    # def _process_treadmill_running(self, sub_sport, activity_id, json_data):
    #     root_logger.debug("treadmill_running for %d: %r", activity_id, json_data)
    #     self._process_steps_activity(sub_sport, activity_id, json_data)

    def _process_running(self, sub_sport, activity_id, json_data):
        root_logger.debug("running (%s) for %d: %r", sub_sport, activity_id, json_data)
        self._process_steps_activity(sub_sport, activity_id, json_data)

    def _process_walking(self, sub_sport, activity_id, json_data):
        root_logger.debug("walking (%s) for %d: %r", sub_sport, activity_id, json_data)
        self._process_steps_activity(sub_sport, activity_id, json_data)

    def _process_fitness_equipment(self, sub_sport, activity_id, json_data):
        root_logger.debug("fitness_equipment (%s) for %d: %r", sub_sport, activity_id, json_data)
        self._call_process_func(sub_sport.name, None, activity_id, json_data)

    def _process_json(self, json_data):
        activity_id = json_data['activityId']
        metadata_dto = json_data['metadataDTO']
        summary_dto = json_data['summaryDTO']
        sport, sub_sport = GarminConnectEnums.get_details_sport(json_data)
        avg_temperature = self._get_field_obj(summary_dto, 'averageTemperature', Fit.Temperature.from_celsius)
        activity = {
            'activity_id'               : activity_id,
            'course_id'                 : self._get_field(metadata_dto, 'associatedCourseId', int),
            'avg_temperature'           : avg_temperature.c_or_f(self.measurement_system) if avg_temperature is not None else None,
        }
        GarminDB.Activities.s_insert_or_update(self.garmin_act_db_session, activity, ignore_none=True)
        self._call_process_func(sport.name, sub_sport, activity_id, json_data)
        return 1

    def process(self):
        """Import data from files into the database."""
        with self.garmin_act_db.managed_session() as self.garmin_act_db_session:
            self._process_files()
