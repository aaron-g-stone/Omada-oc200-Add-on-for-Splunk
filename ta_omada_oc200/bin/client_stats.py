import import_declare_test

import sys

from splunklib import modularinput as smi
from client_stats_helper import stream_events, validate_input


class CLIENT_STATS(smi.Script):
    def __init__(self):
        super(CLIENT_STATS, self).__init__()

    def get_scheme(self):
        scheme = smi.Scheme('client_stats')
        scheme.description = 'Client Stats (traffic/activity metrics by IP)'
        scheme.use_external_validation = True
        scheme.streaming_mode_xml = True
        scheme.use_single_instance = False

        scheme.add_argument(
            smi.Argument(
                'name',
                title='Name',
                description='Name',
                required_on_create=True
            )
        )
        scheme.add_argument(
            smi.Argument(
                'account',
                required_on_create=True,
            )
        )
        scheme.add_argument(
            smi.Argument(
                'fields',
                required_on_create=False,
            )
        )
        scheme.add_argument(
            smi.Argument(
                'site_filter',
                required_on_create=False,
            )
        )
        return scheme

    def validate_input(self, definition: smi.ValidationDefinition):
        return validate_input(definition)

    def stream_events(self, inputs: smi.InputDefinition, ew: smi.EventWriter):
        return stream_events(inputs, ew)


if __name__ == '__main__':
    exit_code = CLIENT_STATS().run(sys.argv)
    sys.exit(exit_code)