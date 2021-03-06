import sys
import json
from datetime import datetime, timedelta

from awscli.arguments import CustomArgument
from awscli.argprocess import uri_param
from awscli.customizations.commands import BasicCommand
from awscli.customizations.datapipeline import translator


HELP_TEXT = """\
The JSON pipeline definition.  If the pipeline definition
is in a file you can use the file://<filename> syntax to
specify a filename.
"""


class DocSectionNotFoundError(Exception):
    pass


def register_customizations(cli):
    cli.register(
        'building-argument-table.datapipeline.put-pipeline-definition',
        add_pipeline_definition)
    cli.register(
        'after-call.datapipeline.GetPipelineDefinition',
        translate_definition)
    cli.register(
        'building-command-table.datapipeline',
        register_commands)
    cli.register(
        'doc-output.datapipeline.get-pipeline-definition',
        document_translation)


def register_commands(command_table, session, **kwargs):
    command_table['list-runs'] = ListRunsCommand(session)


def document_translation(help_command, **kwargs):
    # Remove all the writes until we get to the output.
    # I don't think this is the ideal way to do this, we should
    # improve our plugin/doc system to make this easier.
    doc = help_command.doc
    current = ''
    while current != '======\nOutput\n======':
        try:
            current = doc.pop_write()
        except IndexError:
            # This should never happen, but in the rare case that it does
            # we should be raising something with a helpful error message.
            raise DocSectionNotFoundError(
                'Could not find the "output" section for the command: %s'
                % help_command)
    doc.write('======\nOutput\n======')
    doc.write(
        '\nThe output of this command is the pipeline definition, which'
        ' is documented in the '
        '`Pipeline Definition File Syntax '
        '<http://docs.aws.amazon.com/datapipeline/latest/DeveloperGuide/'
        'dp-writing-pipeline-definition.html>`__')


def add_pipeline_definition(argument_table, **kwargs):
    argument_table['pipeline-definition'] = PipelineDefinitionArgument(
        'pipeline-definition', required=True, help_text=HELP_TEXT)
    # The pipeline-objects is no longer needed required because
    # a user can provide a pipeline-definition instead.
    # get-pipeline-definition also displays the output in the
    # translated format.
    del argument_table['pipeline-objects']


def translate_definition(operation, http_response, parsed, **kwargs):
    api_objects = parsed.pop('pipelineObjects', None)
    if api_objects is None:
        return
    else:
        definition = translator.api_to_definition(api_objects)
        parsed['objects'] = definition['objects']


def convert_described_objects(api_describe_objects, sort_key_func=None):
    # We need to take a field list that looks like this:
    # {u'key': u'@sphere', u'stringValue': u'INSTANCE'},
    # into {"@sphere": "INSTANCE}.
    # We convert the fields list into a field dict.
    converted = []
    for obj in api_describe_objects:
        new_fields = {
            '@id': obj['id'],
            'name': obj['name'],
        }
        for field in obj['fields']:
            new_fields[field['key']] = field.get('stringValue',
                                                 field.get('refValue'))
        converted.append(new_fields)
    if sort_key_func is not None:
        converted.sort(key=sort_key_func)
    return converted


class QueryArgBuilder(object):
    """Convert CLI arguments to Query arguments used by QueryObject.

    """
    def __init__(self, current_time=None):
        if current_time is None:
            current_time = datetime.utcnow()
        self.current_time = current_time

    def build_query(self, parsed_args):
        selectors = []
        if parsed_args.start_interval is None and \
                parsed_args.schedule_interval is None:
            # If no intervals are specified, default
            # to a start time of 4 days ago and an end time
            # of right now.
            end_datetime = self.current_time
            start_datetime = end_datetime - timedelta(days=4)
            start_time_str = start_datetime.strftime('%Y-%m-%dT%H:%M:%S')
            end_time_str = end_datetime.strftime('%Y-%m-%dT%H:%M:%S')
            selectors.append({
                'fieldName': '@actualStartTime',
                    'operator': {
                        'type': 'BETWEEN',
                        'values': [start_time_str, end_time_str]
                    }
            })
        else:
            self._build_schedule_times(selectors, parsed_args)
        if parsed_args.status is not None:
            self._build_status(selectors, parsed_args)
        query = {'selectors': selectors}
        return query

    def _build_schedule_times(self, selectors, parsed_args):
        if parsed_args.start_interval is not None:
            start_time_str = parsed_args.start_interval[0]
            end_time_str = parsed_args.start_interval[1]
            selectors.append({
                'fieldName': '@actualStartTime',
                    'operator': {
                        'type': 'BETWEEN',
                        'values': [start_time_str, end_time_str]
                    }
            })
        if parsed_args.schedule_interval is not None:
            start_time_str = parsed_args.schedule_interval[0]
            end_time_str = parsed_args.schedule_interval[1]
            selectors.append({
                'fieldName': '@scheduleStartTime',
                    'operator': {
                        'type': 'BETWEEN',
                        'values': [start_time_str, end_time_str]
                    }
            })

    def _build_status(self, selectors, parsed_args):
        selectors.append({
            'fieldName': '@status',
            'operator': {
                'type': 'EQ',
                'values': parsed_args.status
            }
        })

class PipelineDefinitionArgument(CustomArgument):
    def add_to_params(self, parameters, value):
        if value is None:
            return
        parsed = json.loads(value)
        api_objects = translator.definition_to_api(parsed)
        parameters['pipeline_objects'] = api_objects


class ListRunsCommand(BasicCommand):
    NAME = 'list-runs'
    DESCRIPTION = (
        'Lists the times the specified pipeline has run. '
        'You can optionally filter the complete list of '
        'results to include only the runs you are interested in.')
    ARG_TABLE = [
        {'name': 'pipeline-id', 'help_text': 'The identifier of the pipeline.',
         'action': 'store', 'required': True, 'cli_type_name': 'string',},
        {'name': 'status',
         'help_text': (
             'Filters the list to include only runs in the specified statuses. '
             'The valid statuses are as follows: waiting, pending, cancelled, '
             'running, finished, failed, waiting_for_runner, '
             'and waiting_on_dependencies. You can combine statuses as a '
             'comma-separated list.  For example: '
             '<code>--status pending,waiting_on_dependencies</code>'),
         'action': 'store'},
        {'name': 'start-interval',
         'help_text': (
             'Filters the list to include only runs that started '
             'within the specified interval.'),
         'action': 'store', 'required': False, 'cli_type_name': 'string',},
        {'name': 'schedule-interval',
         'help_text': (
             'Filters the list to include only runs that are scheduled to '
             'start within the specified interval.'),
         'action': 'store', 'required': False, 'cli_type_name': 'string',},
    ]
    VALID_STATUS = ['waiting', 'pending', 'cancelled', 'running',
                    'finished', 'failed', 'waiting_for_runner',
                    'waiting_on_dependencies']

    def __init__(self, session, formatter=None):
        super(ListRunsCommand, self).__init__(session)
        if formatter is None:
            formatter = ListRunsFormatter()
        self._formatter = formatter

    def _run_main(self, parsed_args, parsed_globals, **kwargs):
        self._set_session_objects(parsed_globals)
        self._parse_type_args(parsed_args)
        self._list_runs(parsed_args)

    def _set_session_objects(self, parsed_globals):
        # This is called from _run_main and is used to ensure that we have
        # a service/endpoint object to work with.
        self.service = self._session.get_service('datapipeline')
        self.endpoint = self.service.get_endpoint(
            region_name=parsed_globals.region,
            endpoint_url=parsed_globals.endpoint_url,
            verify=parsed_globals.verify_ssl)

    def _parse_type_args(self, parsed_args):
        # TODO: give good error messages!
        # Parse the start/schedule times.
        # Parse the status csv.
        if parsed_args.start_interval is not None:
            parsed_args.start_interval = [
                arg.strip() for arg in parsed_args.start_interval.split(',')]
        if parsed_args.schedule_interval is not None:
            parsed_args.schedule_interval = [
                arg.strip() for arg in parsed_args.schedule_interval.split(',')]
        if parsed_args.status is not None:
            parsed_args.status = [
                arg.strip() for arg in parsed_args.status.split(',')]
            self._validate_status_choices(parsed_args.status)

    def _validate_status_choices(self, statuses):
        for status in statuses:
            if status not in self.VALID_STATUS:
                raise ValueError("Invalid status: %s, must be one of: %s" %
                                 (status, ', '.join(self.VALID_STATUS)))

    def _list_runs(self, parsed_args):
        query = QueryArgBuilder().build_query(parsed_args)
        object_ids = self._query_objects(parsed_args.pipeline_id, query)
        objects = self._describe_objects(parsed_args.pipeline_id, object_ids)[
            'pipelineObjects']
        converted = convert_described_objects(
            objects,
            sort_key_func=lambda x: (x.get('@scheduledStartTime'),
                                     x.get('name')))
        self._formatter.display_objects_to_user(converted)

    def _describe_objects(self, pipeline_id, object_ids):
        operation = self.service.get_operation('DescribeObjects')
        http_parsed, parsed = operation.call(
            self.endpoint, pipeline_id=pipeline_id, object_ids=object_ids)
        return parsed

    def _query_objects(self, pipeline_id, query):
        operation = self.service.get_operation('QueryObjects')
        paginator = operation.paginate(
            self.endpoint, pipeline_id=pipeline_id,
            sphere='INSTANCE', query=query)
        parsed = paginator.build_full_result()
        return parsed['ids']


class ListRunsFormatter(object):
    TITLE_ROW_FORMAT_STRING = "       %-50.50s  %-19.19s  %-23.23s"
    FIRST_ROW_FORMAT_STRING = "%4d.  %-50.50s  %-19.19s  %-23.23s"
    SECOND_ROW_FORMAT_STRING = "       %-50.50s  %-19.19s  %-19.19s"

    def __init__(self, stream=sys.stdout):
        self._stream = stream

    def display_objects_to_user(self, objects):
        self._print_headers()
        for i, obj in enumerate(objects):
            self._print_row(i, obj)

    def _print_headers(self):
        self._stream.write(self.TITLE_ROW_FORMAT_STRING % (
            "Name", "Scheduled Start", "Status"))
        self._stream.write('\n')
        second_row = (self.SECOND_ROW_FORMAT_STRING % (
            "ID", "Started", "Ended"))
        self._stream.write(second_row)
        self._stream.write('\n')
        self._stream.write('-' * len(second_row))
        self._stream.write('\n')

    def _print_row(self, index, obj):
        logical_name = obj['@componentParent']
        object_id = obj['@id']
        scheduled_start_date = obj.get('@scheduledStartTime', '')
        status = obj.get('@status', '')
        start_date = obj.get('@actualStartTime', '')
        end_date = obj.get('@actualEndTime', '')
        first_row = self.FIRST_ROW_FORMAT_STRING % (
            index + 1, logical_name, scheduled_start_date, status)
        second_row = self.SECOND_ROW_FORMAT_STRING % (
            object_id, start_date, end_date)
        self._stream.write(first_row)
        self._stream.write('\n')
        self._stream.write(second_row)
        self._stream.write('\n\n')
