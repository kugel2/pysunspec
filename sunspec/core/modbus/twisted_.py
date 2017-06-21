import logging
import queue

import attr
import twisted.internet.protocol
import twisted.protocols.policies

__copyright__ = 'Copyright 2017, EPC Power Corp.'
__license__ = 'GPLv2+'


logger = logging.getLogger(__name__)


class RequestTimeoutError(Exception):
    pass


@attr.s
class Request(object):
    data = attr.ib()
    priority = attr.ib()
    state = attr.ib()
    deferred = attr.ib(cmp=False)


class Protocol(twisted.internet.protocol.Protocol,
               twisted.protocols.policies.TimeoutMixin, object):
    def __init__(self, idle_state, receivers, default_priority, timeout=1):
        self._deferred = None

        self.idle_state = idle_state
        self._state = idle_state
        self._previous_state = self._state

        self.receivers = receivers

        self._active = False

        self._request_memory = None
        self._timeout = timeout

        self.requests = queue.PriorityQueue()

        self._transport = None
        self.default_priority = default_priority

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        logger.debug('Entering state {}'.format(new_state))
        self._previous_state = self._state
        self._state = new_state

    def makeConnection(self, transport):
        self._transport = transport
        logger.debug('Protocol.makeConnection(): {}'.format(transport))

    def _start_transaction(self, deferred):
        logging.debug('_start_transaction()')
        if self._active:
            raise Exception('Protocol is already active')

        self._deferred = deferred
        self._active = True

    def _transaction_over(self):
        # TODO: make delay optional
        twisted.internet.reactor.callLater(0.02, self._transaction_over_after_delay)

        d = self._deferred

        self._deferred = None
        self._request_memory = None

        self.state = self.idle_state

        return d

    def _transaction_over_after_delay(self):
        self._active = False
        logging.debug('_transaction_over_after_delay()')
        self._get()

    def request(self, data, state, priority=None):
        if priority is None:
            priority = self.default_priority
        deferred = twisted.internet.defer.Deferred()

        self._put(Request(
            data=data,
            state=state,
            deferred=deferred,
            priority=priority,
        ))

        return deferred

    def _put(self, request):
        self.requests.put(request)
        self._get()

    def _get(self):
        logging.debug('_get(): active is {}'.format(self._active))
        if not self._active:
            try:
                request = self.requests.get(block=False)
            except queue.Empty:
                pass
            else:
                self._transmit_request(request)

    def _transmit_request(self, request):
        self._start_transaction(deferred=request.deferred)
        self.state = request.state

        logging.debug('_transmit_request()')
        logging.debug('{} --'.format(' '.join('{:02x}'.format(b) for b in request.data)))
        self._transport.write(request.data)
        self.setTimeout(self._timeout)

        self._request_memory = request

    def dataReceived(self, data):
        logger.debug('dataReceived({})\n'.format(len(data)))
        if not self._active:
            return

        if self._deferred is None:
            return

        request = self._request_memory
        if request is None:
            return

        # TODO: add received data filtering ability
        # if not (msg.arbitration_id == request.frame.status_frame.id and
        #             bool(msg.id_type) == request.frame.status_frame.extended):
        #     return

        data = self.receivers[self.state](data)

        # TODO: this doesn't seem a terribly clean way to catch the errbacks in the receiver
        if self._deferred is None:
            return

        if data is not None:
            self.setTimeout(None)
            logging.debug('dataReceived() calling back\n')
            self.callback(data)

    def timeoutConnection(self):
        request = self._request_memory

        # TODO: add descriptive message to request
        message = ('Protocol timed out while in state {}'.format(
            self.state,
        ))
        logger.debug(message)

        if self._previous_state in [self.idle_state]:
            self.state = self._previous_state
        deferred = self._transaction_over()
        deferred.errback(RequestTimeoutError(message))

    def callback(self, payload):
        deferred = self._transaction_over()
        logger.debug('calling back for {}'.format(deferred))
        deferred.callback(payload)

    def errback(self, payload):
        deferred = self._transaction_over()
        logger.debug('erring back for {}'.format(deferred))
        logger.debug('with payload {}'.format(payload))
        deferred.errback(payload)

    def cancel(self):
        self.setTimeout(None)
        deferred = self._transaction_over()
        deferred.cancel()
