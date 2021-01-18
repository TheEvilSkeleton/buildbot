# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


from twisted.internet import defer
from twisted.python import failure

from buildbot import config
from buildbot.process.properties import Interpolate
from buildbot.process.properties import Properties
from buildbot.process.results import CANCELLED
from buildbot.process.results import EXCEPTION
from buildbot.process.results import FAILURE
from buildbot.process.results import RETRY
from buildbot.process.results import SKIPPED
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.reporters.base import ReporterBase
from buildbot.reporters.generators.build import BuildStartEndStatusGenerator
from buildbot.reporters.message import MessageFormatterRenderable
from buildbot.util import httpclientservice
from buildbot.util.logger import Logger
from buildbot.warnings import warn_deprecated

log = Logger()


class GerritVerifyStatusPush(ReporterBase):
    name = "GerritVerifyStatusPush"
    # overridable constants
    RESULTS_TABLE = {
        SUCCESS: 1,
        WARNINGS: 1,
        FAILURE: -1,
        SKIPPED: 0,
        EXCEPTION: 0,
        RETRY: 0,
        CANCELLED: 0
    }
    DEFAULT_RESULT = -1

    def checkConfig(self, baseURL, auth, startDescription=None, endDescription=None,
                    verification_name=None, abstain=False, category=None, reporter=None,
                    verbose=False, wantProperties=True,
                    builders=None, debug=None, verify=None,
                    wantSteps=False, wantPreviousBuild=False, wantLogs=False, generators=None,
                    **kwargs):
        old_arg_names = {
            'startDescription': startDescription is not None,
            'endDescription': endDescription is not None,
            'wantProperties': wantProperties is not True,
            'builders': builders is not None,
            'wantSteps': wantSteps is not False,
            'wantPreviousBuild': wantPreviousBuild is not False,
            'wantLogs': wantLogs is not False,
        }

        passed_old_arg_names = [k for k, v in old_arg_names.items() if v]

        if passed_old_arg_names:

            old_arg_names_msg = ', '.join(passed_old_arg_names)
            if generators is not None:
                config.error(("can't specify generators and deprecated {} arguments ({}) at the "
                              "same time").format(self.__class__.__name__, old_arg_names_msg))
            warn_deprecated('2.10.0',
                            ('The arguments {} passed to {} have been deprecated. Use generators '
                             'instead').format(old_arg_names_msg, self.__class__.__name__))

        if generators is None:
            generators = self._create_generators_from_old_args(builders, wantProperties, wantSteps,
                                                               wantPreviousBuild, wantLogs,
                                                               startDescription, endDescription)

        super().checkConfig(generators=generators, **kwargs)
        httpclientservice.HTTPClientService.checkAvailable(self.__class__.__name__)

    @defer.inlineCallbacks
    def reconfigService(self, baseURL, auth, startDescription=None, endDescription=None,
                        verification_name=None, abstain=False, category=None, reporter=None,
                        verbose=False, wantProperties=True,
                        builders=None, debug=None, verify=None,
                        wantSteps=False, wantPreviousBuild=False, wantLogs=False, generators=None,
                        **kwargs):
        auth = yield self.renderSecrets(auth)
        self.debug = debug
        self.verify = verify
        self.verbose = verbose

        if generators is None:
            generators = self._create_generators_from_old_args(builders, wantProperties, wantSteps,
                                                               wantPreviousBuild, wantLogs,
                                                               startDescription, endDescription)

        yield super().reconfigService(generators=generators, **kwargs)

        if baseURL.endswith('/'):
            baseURL = baseURL[:-1]

        self._http = yield httpclientservice.HTTPClientService.getService(
            self.master, baseURL, auth=auth,
            debug=self.debug, verify=self.verify)

        self._verification_name = verification_name or Interpolate(
            '%(prop:buildername)s')
        self._reporter = reporter or "buildbot"
        self._abstain = abstain
        self._category = category
        self._verbose = verbose

    def _create_generators_from_old_args(self, builders, wantProperties, wantSteps,
                                         wantPreviousBuild, wantLogs,
                                         startDescription, endDescription):
        # wantProperties is ignored, because MessageFormatterRenderable always wants properties.
        # wantSteps and wantPreviousBuild are ignored ignored, because they are not used in
        # this reporter.
        start_formatter = MessageFormatterRenderable(startDescription or 'Build started.')
        end_formatter = MessageFormatterRenderable(endDescription or 'Build done.')

        return [
            BuildStartEndStatusGenerator(builders=builders, add_logs=wantLogs,
                                         start_formatter=start_formatter,
                                         end_formatter=end_formatter)
        ]

    def createStatus(self,
                     change_id,
                     revision_id,
                     name,
                     value,
                     abstain=None,
                     rerun=None,
                     comment=None,
                     url=None,
                     reporter=None,
                     category=None,
                     duration=None):
        """
        Abstract the POST REST api documented here:
        https://gerrit.googlesource.com/plugins/verify-status/+/master/src/main/resources/Documentation/rest-api-changes.md

        :param change_id: The change_id for the change tested (can be in the long form e.g:
            myProject~master~I8473b95934b5732ac55d26311a706c9c2bde9940 or in the short
            integer form).
        :param revision_id: the revision_id tested can be the patchset number or
            the commit id (short or long).
        :param name: The name of the job.
        :param value: The pass/fail result for this job: -1: fail 0: unstable, 1: succeed
        :param abstain: Whether the value counts as a vote (defaults to false)
        :param rerun: Whether this result is from a re-test on the same patchset
        :param comment: A short comment about this job
        :param url: The url link to more info about this job
        :reporter: The user that verified this job
        :category: A category for this job
        "duration": The time it took to run this job

        :return: A deferred with the result from Gerrit.
        """
        payload = {'name': name, 'value': value}

        if abstain is not None:
            payload['abstain'] = abstain

        if rerun is not None:
            payload['rerun'] = rerun

        if comment is not None:
            payload['comment'] = comment

        if url is not None:
            payload['url'] = url

        if reporter is not None:
            payload['reporter'] = reporter

        if category is not None:
            payload['category'] = category

        if duration is not None:
            payload['duration'] = duration

        if self._verbose:
            log.debug(
                'Sending Gerrit status for {change_id}/{revision_id}: data={data}',
                change_id=change_id,
                revision_id=revision_id,
                data=payload)

        return self._http.post(
            '/'.join([
                '/a/changes', str(change_id), 'revisions', str(revision_id),
                'verify-status~verifications'
            ]),
            json=payload)

    def formatDuration(self, duration):
        """Format the duration.

        This method could be overridden if really needed, as the duration format in gerrit
        is an arbitrary string.
        :param duration: duration in timedelta
        """
        days = duration.days
        hours, remainder = divmod(duration.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if days:
            return '{} day{} {}h {}m {}s'.format(days, "s" if days > 1 else "",
                                                 hours, minutes, seconds)
        elif hours:
            return '{}h {}m {}s'.format(hours, minutes, seconds)
        return '{}m {}s'.format(minutes, seconds)

    @staticmethod
    def getGerritChanges(props):
        """ Get the gerrit changes

            This method could be overridden if really needed to accommodate for other
            custom steps method for fetching gerrit changes.

            :param props: an IProperty

            :return: (optionally via deferred) a list of dictionary with at list
                change_id, and revision_id,
                which format is the one accepted by the gerrit REST API as of
                /changes/:change_id/revision/:revision_id paths (see gerrit doc)
        """
        if 'gerrit_changes' in props:
            return props.getProperty('gerrit_changes')

        if 'event.change.number' in props:
            return [{
                'change_id': props.getProperty('event.change.number'),
                'revision_id': props.getProperty('event.patchSet.number')
            }]
        return []

    @defer.inlineCallbacks
    def send(self, build):
        # the only case when this function is called is when the user derives this class, overrides
        # send() and calls super().send(build) from there.
        yield self._send_impl(build, self._cached_report)

    @defer.inlineCallbacks
    def sendMessage(self, reports):
        build = reports[0]['builds'][0]
        if self.send.__func__ is not GerritVerifyStatusPush.send:
            warn_deprecated('2.9.0', 'send() in reporters has been deprecated. Use sendMessage()')
            self._cached_report = reports[0]
            yield self.send(build)
        else:
            yield self._send_impl(build, reports[0])

    @defer.inlineCallbacks
    def _send_impl(self, build, report):
        props = Properties.fromDict(build['properties'])
        props.master = self.master

        comment = report.get('body', None)

        if build['complete']:
            value = self.RESULTS_TABLE.get(build['results'],
                                           self.DEFAULT_RESULT)
            duration = self.formatDuration(build['complete_at'] - build['started_at'])
        else:
            value = 0
            duration = 'pending'

        name = yield props.render(self._verification_name)
        reporter = yield props.render(self._reporter)
        category = yield props.render(self._category)
        abstain = yield props.render(self._abstain)
        # TODO: find reliable way to find out whether its a rebuild
        rerun = None

        changes = yield self.getGerritChanges(props)
        for change in changes:
            try:
                yield self.createStatus(
                    change['change_id'],
                    change['revision_id'],
                    name,
                    value,
                    abstain=abstain,
                    rerun=rerun,
                    comment=comment,
                    url=build['url'],
                    reporter=reporter,
                    category=category,
                    duration=duration)
            except Exception:
                log.failure(
                    'Failed to send status!', failure=failure.Failure())
