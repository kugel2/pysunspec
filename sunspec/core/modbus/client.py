
"""
    Copyright (C) 2017 SunSpec Alliance

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the "Software"),
    to deal in the Software without restriction, including without limitation
    the rights to use, copy, modify, merge, publish, distribute, sublicense,
    and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
    THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
    IN THE SOFTWARE.
"""
from __future__ import division
from builtins import str
from builtins import range
from builtins import object
from builtins import bytes
from builtins import super

import attr
import enum
import logging
import os
import socket
import struct
import serial
try:
    import twisted.internet
    import twisted.internet.serialport
    import sunspec.core.modbus.twisted_
except ImportError:
    twisted = None

try:
    import xml.etree.ElementTree as ET
except:
    import elementtree.ElementTree as ET

import sunspec.core.modbus.mbmap as mbmap
import sunspec.core.util as util

PARITY_NONE = 'N'
PARITY_EVEN = 'E'

REQ_COUNT_MAX = 125

FUNC_READ_HOLDING = 3
FUNC_READ_INPUT = 4
FUNC_WRITE_MULTIPLE = 16

TEST_NAME = 'test_name'

modbus_rtu_clients = {}

class ModbusClientError(Exception):
    pass

class ModbusClientTimeout(ModbusClientError):
    pass


@attr.s
class _ModbusClientException(Exception):
    code = attr.ib()
    text = attr.ib()
    details = attr.ib()

class ModbusClientException(_ModbusClientException, enum.Enum):
    illegal_function = (
        1,
        'Illegal Function',
        'Function code received in the query is not recognized or allowed by slave'
    )
    illegal_data_address = (
        2,
        'Illegal Data Address',
        'Data address of some or all the required entities are not allowed or do not exist in slave'
    )
    illegal_data_value = (
        3,
        'Illegal Data Value',
        'Value is not accepted by slave'
    )
    slave_device_failure = (
        4,
        'Slave Device Failure',
        'Unrecoverable error occurred while slave was attempting to perform requested action'
    )
    acknowledge = (
        5,
        'Acknowledge',
        'Slave has accepted request and is processing it, but a long duration of time is required. This response is returned to prevent a timeout error from occurring in the master. Master can next issue a Poll Program Complete message to determine whether processing is completed'
    )
    slave_device_busy = (
        6,
        'Slave Device Busy',
        'Slave is engaged in processing a long-duration command. Master should retry later'
    )
    negative_acknowledge = (
        7,
        'Negative Acknowledge',
        'Slave cannot perform the programming functions. Master should request diagnostic or error information from slave'
    )
    memory_parity_error = (
        8,
        'Memory Parity Error',
        'Slave detected a parity error in memory. Master can retry the request, but service may be required on the slave device'
    )
    gateway_path_unavailable = (
        10,
        'Gateway Path Unavailable',
        'Specialized for Modbus gateways. Indicates a misconfigured gateway'
    )
    gateway_target_device_failed_to_respond = (
        11,
        'Gateway Target Device Failed to Respond',
        'Specialized for Modbus gateways. Sent when slave fails to respond'
    )

    def __str__(self):
        return '[Code {code}] {text}: {details}'.format(
            **attr.asdict(self.value)
        )

    @classmethod
    def from_code(cls, code):
        error, = (e for e in cls if e.code == code)

        return error

def modbus_rtu_client(name=None, baudrate=None, parity=None, cls=None):

    global modbus_rtu_clients

    client = modbus_rtu_clients.get(name)
    if client is not None:
        if baudrate is not None and client.baudrate != baudrate:
            raise ModbusClientError('Modbus client baudrate mismatch')
        if parity is not None and client.parity != parity:
            raise ModbusClientError('Modbus client parity mismatch')
        if cls is not None and type(client) != cls:
            raise ModbusClientError('Modbus client class mismatch')
    else:
        if baudrate is None:
            baudrate = 9600
        if parity is None:
            parity = PARITY_NONE
        if cls is None:
            cls = ModbusClientRTU

        client = cls(name, baudrate, parity)
        modbus_rtu_clients[name] = client

    return client

def modbus_rtu_client_remove(name=None):

    global modbus_rtu_clients

    if modbus_rtu_clients.get(name):
        del modbus_rtu_clients[name]

def pack_rtu_read_request(slave_id, addr, count, op):
    req = struct.pack('>BBHH', int(slave_id), op, int(addr), int(count))
    req = bytes(req)
    req += struct.pack('>H', computeCRC(req))
    return req

def pack_rtu_write_request(slave_id, addr, data, func):
    req = struct.pack('>BBHHB', int(slave_id), func, int(addr),
                      len(data) // 2, len(data))
    req = bytes(req)
    req += data
    req += struct.pack('>H', computeCRC(req))
    return req


@enum.unique
class State(enum.Enum):
    idle = 0
    reading = 1
    writing = 2


@enum.unique
class Priority(enum.IntEnum):
    default = 0


if twisted is not None:
    class ModbusClientRTUTwistedProtocol(sunspec.core.modbus.twisted_.Protocol):
        def __init__(self):
            super().__init__(
                idle_state=State.idle,
                receivers={
                    State.idle: None,
                    State.reading: self._data_received,
                    State.writing: self._data_received,
                },
                default_priority=Priority.default,
                timeout=5,
            )

            self.data = None
            self.remaining = None

            self.response_length = None
            self.length_found = False

            self._slave_id = None
            self._addr = None
            self._trace_func = False

            self._function_code = None

        def _transmit_request(self, request):
            self.data = bytearray()
            self.response_length = 5
            self.length_found = False

            # TODO: maybe flush the input buffer?  but with the queueing behind this
            #       this isn't the place to do so if we do
            # self._transport.flushInput()

            super()._transmit_request(request=request)

        @twisted.internet.defer.inlineCallbacks
        def read(self, slave_id, addr, count, op, trace_func, max_count):
            data = bytearray()

            div, mod = divmod(count, max_count)

            counts = (max_count,) * div
            if mod > 0:
                counts += (mod,)

            for c in counts:
                d = yield self._read(slave_id, addr, c, op, trace_func)
                data.extend(d)
                addr += c

            twisted.internet.defer.returnValue(bytes(data))

        def _read(self, slave_id, addr, count, op, trace_func):
            self._slave_id = slave_id
            self._addr = addr
            self._trace_func = trace_func

            req = pack_rtu_read_request(slave_id, addr, count, op)

            if self._trace_func:
                s = '%s:%s[addr=%s] ->' % (self.name, str(slave_id), addr)
                for c in req:
                    s += '%02X' % (ord(c))
                self._trace_func(s)

            return self.request(req, state=State.reading)

        @twisted.internet.defer.inlineCallbacks
        def write(self, slave_id, addr, data, trace_func, max_count):
            for d in util.chunker(data, max_count):
                yield self._write(slave_id, addr, bytes(d), trace_func)

        def _write(self, slave_id, addr, data, trace_func):
            self._slave_id = slave_id
            self._addr = addr
            self._trace_func = trace_func
            self._function_code = FUNC_WRITE_MULTIPLE

            req = pack_rtu_write_request(slave_id, addr, data, self._function_code)

            if self._trace_func:
                s = '%s:%s[addr=%s] ->' % (self.name, str(slave_id), addr)
                for c in req:
                    s += '%02X' % (ord(c))
                self._trace_func(s)

            logging.debug(' '.join('{:02x}'.format(b) for b in req))
            return self.request(req, state=State.writing)

        def _data_received(self, data):
            self.data.extend(data)

            if not self.length_found and len(self.data) >= self.response_length:
                if not (self.data[1] & 0x80):
                    if self._state is State.reading:
                        self.response_length += self.data[2]
                    elif self._state is State.writing:
                        self.response_length = 8
                    self.length_found = True
                else:
                    self.errback(ModbusClientException.from_code(self.data[2]))
                    return

            if len(self.data) < self.response_length:
                return None

            logging.debug('done after {} (expected {})\n'.format(
                len(self.data),
                self.response_length,
            ))

            # TODO: this seems fishy at best
            self.data = self.data[:self.response_length]

            received_crc = (self.data[-2] << 8) | self.data[-1]
            calculated_crc = computeCRC(self.data[:-2])

            if calculated_crc != received_crc:
                raise ModbusClientError(
                    'CRC error: calculated {} but received {}'.format(
                        calculated_crc,
                        received_crc,
                    )
                )

            # TODO: would have to be above the fishy stuff
            if len(self.data) > self.response_length:
                raise ModbusClientException.from_code(self.data[2])

            if self._trace_func:
                s = '%s:%s[addr=%s] <--' % (self.name, str(self._slave_id), self._addr)
                for c in self.data:
                    s += '%02X' % c
                self._trace_func(s)

            if self._state is State.reading:
                return self.data[3:-2]
            elif self._state is State.writing:
                x = struct.unpack('>BBHHH', self.data)
                resp_slave_id, resp_func, resp_addr, resp_count, resp_crc = x

                if resp_slave_id != self._slave_id or resp_func != self._function_code or resp_addr != self._addr: # TODO: or resp_count != count:
                    raise ModbusClientError('Modbus response format error')

                return x


    class ModbusClientRTUTwisted(object):
        def __init__(self, name='/dev/ttyUSB0', baudrate=9600, parity=None):
            self.name = name
            self.baudrate = baudrate
            self.parity = parity
            self.serial = None
            self.timeout = .5
            self.write_timeout = .5
            self.devices = {}

            self.protocol = ModbusClientRTUTwistedProtocol()

            self.open()

        def open(self):
            """Open the RTU client serial interface.
            """
            if self.parity == PARITY_EVEN:
                parity = twisted.internet.serialport.PARITY_EVEN
            else:
                parity = twisted.internet.serialport.PARITY_NONE

            if self.name != TEST_NAME:
                self.serial = twisted.internet.serialport.SerialPort(
                    protocol=self.protocol,
                    deviceNameOrPortNumber=self.name,
                    reactor=twisted.internet.reactor,
                    baudrate=self.baudrate,
                    bytesize=twisted.internet.serialport.EIGHTBITS,
                    parity=parity,
                    stopbits=twisted.internet.serialport.STOPBITS_ONE,
                    xonxoff=False,
                    rtscts=False,
                )

        def add_device(self, slave_id, device):
            """Add a device to the RTU client.

            Parameters:

                slave_id :
                    Modbus slave id.

                device :
                    Device to add to the client.
            """

            self.devices[slave_id] = device

        def remove_device(self, slave_id):
            """Remove a device from the RTU client.

            Parameters:

                slave_id :
                    Modbus slave id.
            """

            if self.devices.get(slave_id):
                del self.devices[slave_id]

            # if no more devices using the client interface, close and remove the client
            if len(self.devices) == 0:
                self.close()
                modbus_rtu_client_remove(self.name)

        def read(self, slave_id, addr, count, op=FUNC_READ_HOLDING, trace_func=None, max_count=REQ_COUNT_MAX):
            return self.protocol.read(slave_id, addr, count, op, trace_func, max_count)

        def write(self, slave_id, addr, data, trace_func=None, max_count=REQ_COUNT_MAX):
            return self.protocol.write(slave_id, addr, data, trace_func, max_count)


class ModbusClientRTU(object):
    """A Modbus RTU client that multiple devices can use to access devices over
    the same serial interface. Currently, the implementation does not support
    concurent device requests so the support of multiple devices must be single
    threaded.

    Parameters:

        name :
            Name of the serial port such as 'com4' or '/dev/ttyUSB0'.

        baudrate :
            Baud rate such as 9600 or 19200. Default is 9600 if not specified.

        parity :
            Parity. Possible values:
                :const:`sunspec.core.modbus.client.PARITY_NONE`,
                :const:`sunspec.core.modbus.client.PARITY_EVEN`.  Defaults to
                :const:`PARITY_NONE`.

    Raises:

        ModbusClientError: Raised for any general modbus client error.

        ModbusClientTimeoutError: Raised for a modbus client request timeout.

        ModbusClientException: Raised for an exception response to a modbus
            client request.

    Attributes:

        name
            Name of the serial port such as 'com4' or '/dev/ttyUSB0'.

        baudrate
            Baud rate.

        parity
            Parity. Possible values:
                :const:`sunspec.core.modbus.client.PARITY_NONE`,
                :const:`sunspec.core.modbus.client.PARITY_EVEN`

        serial
            The pyserial.Serial object used for serial communications.

        timeout
            Read timeout in seconds. Fractional values are permitted.

        write_timeout
            Write timeout in seconds. Fractional values are permitted.

        devices
            List of :const:`sunspec.core.modbus.client.ModbusClientDeviceRTU`
            devices currently using the client.
    """

    def __init__(self, name='/dev/ttyUSB0', baudrate=9600, parity=None):
        self.name = name
        self.baudrate = baudrate
        self.parity = parity
        self.serial = None
        self.timeout = .5
        self.write_timeout = .5
        self.devices = {}

        self.open()

    def open(self):
        """Open the RTU client serial interface.
        """

        try:
            if self.parity == PARITY_EVEN:
                parity = serial.PARITY_EVEN
            else:
                parity = serial.PARITY_NONE

            if self.name != TEST_NAME:
                self.serial = serial.Serial(port = self.name, baudrate=self.baudrate,
                                            bytesize=8, parity=parity,
                                            stopbits=1, xonxoff=0,
                                            timeout=self.timeout, writeTimeout=self.write_timeout)
            else:
                import sunspec.core.test.fake.serial as fake
                self.serial = fake.Serial(port = self.name, baudrate=self.baudrate,
                                          bytesize=8, parity=parity,
                                          stopbits=1, xonxoff=0,
                                          timeout=self.timeout, writeTimeout=self.write_timeout)

        except Exception as e:
            if self.serial is not None:
                self.serial.close()
                self.serial = None
            raise ModbusClientError('Serial init error: %s' % str(e))

    def close(self):
        """Close the RTU client serial interface.
        """

        try:
            if self.serial is not None:
                self.serial.close()
        except Exception as e:
            raise ModbusClientError('Serial close error: %s' % str(e))

    def add_device(self, slave_id, device):
        """Add a device to the RTU client.

        Parameters:

            slave_id :
                Modbus slave id.

            device :
                Device to add to the client.
        """

        self.devices[slave_id] = device

    def remove_device(self, slave_id):
        """Remove a device from the RTU client.

        Parameters:

            slave_id :
                Modbus slave id.
        """

        if self.devices.get(slave_id):
            del self.devices[slave_id]

        # if no more devices using the client interface, close and remove the client
        if len(self.devices) == 0:
            self.close()
            modbus_rtu_client_remove(self.name)

    def _read(self, slave_id, addr, count, op=FUNC_READ_HOLDING, trace_func=None):
        resp = bytes()
        len_remaining = 5
        len_found = False
        except_code = None

        req = pack_rtu_read_request(slave_id, addr, count, op)

        if trace_func:
            s = '%s:%s[addr=%s] ->' % (self.name, str(slave_id), addr)
            for c in req:
                s += '%02X' % (ord(c))
            trace_func(s)

        self.serial.flushInput()
        try:
            self.serial.write(req)
        except Exception as e:
            raise ModbusClientError('Serial write error: %s' % str(e))

        while len_remaining > 0:
            c = self.serial.read(len_remaining)
            len_read = len(c);
            if len_read > 0:
                resp += c
                len_remaining -= len_read
                if len_found is False and len(resp) >= 5:
                    if not (resp[1] & 0x80):
                        len_remaining = (resp[2] + 5) - len(resp)
                        len_found = True
                    else:
                        except_code = resp[2]
            else:
                raise ModbusClientTimeout('Response timeout')

        if trace_func:
            s = '%s:%s[addr=%s] <--' % (self.name, str(slave_id), addr)
            for c in resp:
                s += '%02X' % (ord(c))
            trace_func(s)

        crc = (resp[-2] << 8) | resp[-1]
        if not checkCRC(resp[:-2], crc):
            raise ModbusClientError('CRC error')

        if except_code:
            raise ModbusClientException.from_code(except_code)

        return resp[3:-2]

    def read(self, slave_id, addr, count, op=FUNC_READ_HOLDING, trace_func=None, max_count=REQ_COUNT_MAX):
        """
        Parameters:

            slave_id :
                Modbus slave id.

            addr :
                Starting Modbus address.

            count :
                Read length in Modbus registers.

            op :
                Modbus function code for request. Possible values:
                    :const:`FUNC_READ_HOLDING`, :const:`FUNC_READ_INPUT`.

            trace_func :
                Trace function to use for detailed logging. No detailed logging
                is perform is a trace function is not supplied.

            max_count :
                Maximum register count for a single Modbus request.

        Returns:

            Byte string containing register contents.
        """

        resp = bytes()
        read_count = 0
        read_offset = 0

        if self.serial is not None:
            while (count > 0):
                if count > max_count:
                    read_count = max_count
                else:
                    read_count = count
                data = self._read(slave_id, addr + read_offset, read_count, op=op, trace_func=trace_func)
                if data:
                    resp += data
                    count -= read_count
                    read_offset += read_count
                else:
                    return
        else:
            raise ModbusClientError('Client serial port not open: %s' % self.name)

        return resp

    def _write(self, slave_id, addr, data, trace_func=None):
        resp = bytes()
        len_remaining = 5
        len_found = False
        except_code = None
        func = FUNC_WRITE_MULTIPLE

        req = pack_rtu_write_request(slave_id, addr, data, func)

        if trace_func:
            s = '%s:%s[addr=%s] ->' % (self.name, str(slave_id), addr)
            for c in req:
                s += '%02X' % (ord(c))
            trace_func(s)

        self.serial.flushInput()
        try:
            self.serial.write(req)
        except Exception as e:
            raise ModbusClientError('Serial write error: %s' % str(e))

        while len_remaining > 0:
            c = self.serial.read(len_remaining)
            len_read = len(c);
            if len_read > 0:
                resp += c
                len_remaining -= len_read
                if len_found is False and len(resp) >= 5:
                    if not (resp[1] & 0x80):
                        len_remaining = 8 - len(resp)
                        len_found = True
                    else:
                        except_code = resp[2]
            else:
                raise ModbusClientTimeout('Response timeout')

        if trace_func:
            s = '%s:%s[addr=%s] <--' % (self.name, str(slave_id), addr)
            for c in resp:
                s += '%02X' % c
            trace_func(s)


        crc = (resp[-2] << 8) | resp[-1]
        if not checkCRC(resp[:-2], crc):
            raise ModbusClientError('CRC error')

        if except_code:
            raise ModbusClientExceptionfrom_code(except_code)
        else:
            resp_slave_id, resp_func, resp_addr, resp_count, resp_crc = struct.unpack('>BBHHH', resp)
            if resp_slave_id != slave_id or resp_func != func or resp_addr != addr or resp_count != len(data)//2:
                raise ModbusClientError('Modbus response format error')

    def write(self, slave_id, addr, data, trace_func=None, max_count=REQ_COUNT_MAX):
        """
        Parameters:

            slave_id :
                Modbus slave id.

            addr :
                Starting Modbus address.

            data :
                Byte string containing register contents.

            trace_func :
                Trace function to use for detailed logging. No detailed logging
                is perform is a trace function is not supplied.

            max_count :
                Maximum register count for a single Modbus request.
        """

        write_count = 0
        write_offset = 0
        count = len(data)//2

        if self.serial is not None:
            while (count > 0):
                if count > max_count:
                    write_count = max_count
                else:
                    write_count = count
                self._write(slave_id, addr + write_offset, data[(write_offset * 2):((write_offset + write_count) * 2)], trace_func=trace_func)
                count -= write_count
                write_offset += write_count
        else:
            raise ModbusClientError('Client serial port not open: %s' % self.name)


if twisted is not None:
    class ModbusClientDeviceRTUTwisted(object):
        def __init__(self, slave_id, name, baudrate=None, parity=None, timeout=None, ctx=None, trace_func=None, max_count=None):
            if max_count is None:
                max_count = REQ_COUNT_MAX

            self.slave_id = slave_id
            self.name = name
            self.client = None
            self.ctx = ctx
            self.trace_func = trace_func
            self.max_count = max_count

            self.client = modbus_rtu_client(
                name=name,
                baudrate=baudrate,
                parity=parity,
                cls=ModbusClientRTUTwisted
            )
            if self.client is None:
                raise ModbusClientError('No modbus rtu client set for device')
            self.client.add_device(self.slave_id, self)

            if timeout is not None and self.client.serial is not None:
                self.client.serial.timeout = timeout
                self.client.serial.writeTimeout = timeout

        def close(self):
            """Close the device. Called when device is not longer in use.
            """

            if self.client:
                self.client.remove_device(self.slave_id)

        def read(self, addr, count, op=FUNC_READ_HOLDING):
            """Read Modbus device registers.

            Parameters:

                addr :
                    Starting Modbus address.

                count :
                    Read length in Modbus registers.

                op :
                    Modbus function code for request.

            Returns:

                Byte string containing register contents.
            """

            return self.client.read(self.slave_id, addr, count, op=op, trace_func=self.trace_func, max_count=self.max_count)

        def write(self, addr, data):
            """Write Modbus device registers.

            Parameters:

                addr :
                    Starting Modbus address.

                count :
                    Byte string containing register contents.
            """

            return self.client.write(self.slave_id, addr, data, trace_func=self.trace_func, max_count=self.max_count)


class ModbusClientDeviceRTU(object):
    """Provides access to a Modbus RTU device.

    Parameters:

        slave_id :
            Modbus slave id.

        name :
            Name of the serial port such as 'com4' or '/dev/ttyUSB0'.

        baudrate :
            Baud rate such as 9600 or 19200. Default is 9600 if not specified.

        parity :
            Parity. Possible values:
                :const:`sunspec.core.modbus.client.PARITY_NONE`,
                :const:`sunspec.core.modbus.client.PARITY_EVEN` Defaulted to
                :const:`PARITY_NONE`.

        timeout :
            Modbus request timeout in seconds. Fractional seconds are permitted
            such as .5.

        ctx :
            Context variable to be used by the object creator. Not used by the
            modbus module.

        trace_func :
            Trace function to use for detailed logging. No detailed logging is
            perform is a trace function is not supplied.

        max_count :
            Maximum register count for a single Modbus request.

    Raises:

        ModbusClientError: Raised for any general modbus client error.

        ModbusClientTimeoutError: Raised for a modbus client request timeout.

        ModbusClientException: Raised for an exception response to a modbus
            client request.
    """

    def __init__(self, slave_id, name, baudrate=None, parity=None, timeout=None, ctx=None, trace_func=None, max_count=None):
        if max_count is None:
            max_count = REQ_COUNT_MAX

        self.slave_id = slave_id
        self.name = name
        self.client = None
        self.ctx = ctx
        self.trace_func = trace_func
        self.max_count = max_count

        self.client = modbus_rtu_client(name, baudrate, parity)
        if self.client is None:
            raise ModbusClientError('No modbus rtu client set for device')
        self.client.add_device(self.slave_id, self)

        if timeout is not None and self.client.serial is not None:
            self.client.serial.timeout = timeout
            self.client.serial.writeTimeout = timeout

    def close(self):
        """Close the device. Called when device is not longer in use.
        """

        if self.client:
            self.client.remove_device(self.slave_id)

    def read(self, addr, count, op=FUNC_READ_HOLDING):
        """Read Modbus device registers.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Read length in Modbus registers.

            op :
                Modbus function code for request.

        Returns:

            Byte string containing register contents.
        """

        return self.client.read(self.slave_id, addr, count, op=op, trace_func=self.trace_func, max_count=self.max_count)

    def write(self, addr, data):
        """Write Modbus device registers.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Byte string containing register contents.
        """

        return self.client.write(self.slave_id, addr, data, trace_func=self.trace_func, max_count=self.max_count)

TCP_HDR_LEN = 6
TCP_RESP_MIN_LEN = 3
TCP_HDR_O_LEN = 4
TCP_READ_REQ_LEN = 6
TCP_WRITE_MULT_REQ_LEN = 7

TCP_DEFAULT_PORT = 502
TCP_DEFAULT_TIMEOUT = 2

class ModbusClientDeviceTCP(object):
    """Provides access to a Modbus TCP device.

    Parameters:

        slave_id :
            Modbus slave id.

        ipaddr :
            IP address string.

        ipport :
            IP port.

        timeout :
            Modbus request timeout in seconds. Fractional seconds are permitted
            such as .5.

        ctx :
            Context variable to be used by the object creator. Not used by the
            modbus module.

        trace_func :
            Trace function to use for detailed logging. No detailed logging is
            perform is a trace function is not supplied.

        max_count :
            Maximum register count for a single Modbus request.

        test :
            Use test socket. If True use the fake socket module for network
            communications.


    Raises:

        ModbusClientError: Raised for any general modbus client error.

        ModbusClientTimeoutError: Raised for a modbus client request timeout.

        ModbusClientException: Raised for an exception response to a modbus
            client request.

    Attributes:

        slave_id
            Modbus slave id.

        ipaddr
            Destination device IP address string.

        ipport
            Destination device IP port.

        timeout
            Modbus request timeout in seconds. Fractional seconds are permitted
            such as .5.

        ctx
            Context variable to be used by the object creator. Not used by the
            modbus module.

        socket
            Socket used for network connection. If no connection active, value
            is None.

        trace_func
            Trace function to use for detailed logging.

        max_count
            Maximum register count for a single Modbus request.
    """

    def __init__(self, slave_id, ipaddr, ipport=502, timeout=None, ctx=None, trace_func=None, max_count=REQ_COUNT_MAX, test=False):
        self.slave_id = slave_id
        self.ipaddr = ipaddr
        self.ipport = ipport
        self.timeout = timeout
        self.ctx = ctx
        self.socket = None
        self.trace_func = trace_func
        self.max_count = max_count

        if ipport is None:
            self.ipport = TCP_DEFAULT_PORT
        if timeout is None:
            self.timeout = TCP_DEFAULT_TIMEOUT

        if test:
            import sunspec.core.test.fake.socket as fake
            self.socket = fake.socket()

    def close(self):

        self.disconnect()

    def connect(self, timeout=None):
        """Connect to TCP destination.

        Parameters:

            timeout :
                Connection timeout in seconds.
        """

        if self.socket:
            self.disconnect()

        if timeout is None:
            timeout = self.timeout

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(timeout)
            self.socket.connect((self.ipaddr, self.ipport))
        except Exception as e:
            raise ModbusClientError('Connection error: %s' % str(e))

    def disconnect(self):
        """Disconnect from TCP destination.
        """

        try:
            if self.socket:
                self.socket.close()
            self.socket = None
        except Exception:
            pass

    def _read(self, addr, count, op=FUNC_READ_HOLDING):

        resp = bytes()
        len_remaining = TCP_HDR_LEN + TCP_RESP_MIN_LEN
        len_found = False
        except_code = None

        req = struct.pack('>HHHBBHH', 0, 0, TCP_READ_REQ_LEN, int(self.slave_id), op, int(addr), int(count))

        if self.trace_func:
            s = '%s:%s:%s[addr=%s] ->' % (self.ipaddr, str(self.ipport), str(self.slave_id), addr)
            for c in req:
                s += '%02X' % (ord(c))
            self.trace_func(s)

        try:
            self.socket.sendall(req)
        except Exception as e:
            raise ModbusClientError('Socket write error: %s' % str(e))

        while len_remaining > 0:
            c = self.socket.recv(len_remaining)
            # print 'c = {0}'.format(c)
            len_read = len(c);
            if len_read > 0:
                resp += c
                len_remaining -= len_read
                if len_found is False and len(resp) >= TCP_HDR_LEN + TCP_RESP_MIN_LEN:
                    data_len = struct.unpack('>H', resp[TCP_HDR_O_LEN:TCP_HDR_O_LEN + 2])
                    len_remaining = data_len[0] - (len(resp) - TCP_HDR_LEN)
            else:
                raise ModbusClientError('Response timeout')

        if not (resp[TCP_HDR_LEN + 1] & 0x80):
            len_remaining = (resp[TCP_HDR_LEN + 2] + TCP_HDR_LEN) - len(resp)
            len_found = True
        else:
            except_code = resp[TCP_HDR_LEN + 2]

        if self.trace_func:
            s = '%s:%s:%s[addr=%s] <--' % (self.ipaddr, str(self.ipport), str(self.slave_id), addr)
            for c in resp:
                s += '%02X' % c
            self.trace_func(s)

        if except_code:
            raise ModbusClientException(except_code)

        return resp[(TCP_HDR_LEN + 3):]

    def read(self, addr, count, op=FUNC_READ_HOLDING):
        """ Read Modbus device registers. If no connection exists to the
        destination, one is created and disconnected at the end of the request.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Read length in Modbus registers.

            op :
                Modbus function code for request.

        Returns:

            Byte string containing register contents.
        """

        resp = bytes()
        read_count = 0
        read_offset = 0
        local_connect = False

        if self.socket is None:
            local_connect = True
            self.connect(self.timeout)

        try:
            while (count > 0):
                if count > self.max_count:
                    read_count = self.max_count
                else:
                    read_count = count
                data = self._read(addr + read_offset, read_count, op=op)
                if data:
                    resp += data
                    count -= read_count
                    read_offset += read_count
                else:
                    break
        finally:
            if local_connect:
                self.disconnect()

        return resp

    def _write(self, addr, data):

        resp = bytes()
        len_remaining = TCP_HDR_LEN + TCP_RESP_MIN_LEN
        len_found = False
        except_code = None
        func = FUNC_WRITE_MULTIPLE

        write_len = len(data)
        write_count = write_len//2
        req = struct.pack('>HHHBBHHB', 0, 0, TCP_WRITE_MULT_REQ_LEN + write_len, int(self.slave_id), func, int(addr), write_count, write_len)
        req += data

        if self.trace_func:
            s = '%s:%s:%s[addr=%s] ->' % (self.ipaddr, str(self.ipport), str(self.slave_id), addr)
            for c in req:
                s += '%02X' % (ord(c))
            self.trace_func(s)

        try:
            self.socket.sendall(req)
        except Exception as e:
            raise ModbusClientError('Socket write error: %s' % str(e))

        while len_remaining > 0:
            c = self.socket.recv(len_remaining)
            # print 'c = {0}'.format(c)
            len_read = len(c);
            if len_read > 0:
                resp += c
                len_remaining -= len_read
                if len_found is False and len(resp) >= TCP_HDR_LEN + TCP_RESP_MIN_LEN:
                    data_len = struct.unpack('>H', resp[TCP_HDR_O_LEN:TCP_HDR_O_LEN + 2])
                    len_remaining = data_len[0] - (len(resp) - TCP_HDR_LEN)
            else:
                raise ModbusClientTimeout('Response timeout')

        if not (resp[TCP_HDR_LEN + 1] & 0x80):
            len_remaining = (resp[TCP_HDR_LEN + 2] + TCP_HDR_LEN) - len(resp)
            len_found = True
        else:
            except_code = resp[TCP_HDR_LEN + 2]

        if self.trace_func:
            s = '%s:%s:%s[addr=%s] <--' % (self.ipaddr, str(self.ipport), str(self.slave_id), addr)
            for c in resp:
                s += '%02X' % (ord(c))
            self.trace_func(s)

        if except_code:
            raise ModbusClientException(except_code)

    def write(self, addr, data):
        """ Write Modbus device registers. If no connection exists to the
        destination, one is created and disconnected at the end of the request.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Byte string containing register contents.
        """

        write_count = 0
        write_offset = 0
        local_connect = False
        count = len(data)//2

        if self.socket is None:
            local_connect = True
            self.connect(self.timeout)

        try:
            while (count > 0):
                if count > self.max_count:
                    write_count = self.max_count
                else:
                    write_count = count
                self._write(addr + write_offset, data[(write_offset * 2):((write_offset + write_count) * 2)])
                count -= write_count
                write_offset += write_count
        finally:
            if local_connect:
                self.disconnect()

class ModbusClientDeviceMapped(object):
    """Provides access to a Modbus device implemented as a modbus map (mbmap)
    formatted file.

    Parameters:

        slave_id :
            Modbus slave id.

        name :
            Modbus map file name. Must be in mbmap format.

        pathlist :
            Pathlist object containing alternate paths for modbus map file.

        max_count :
            Maximum register count for a single Modbus request.

        ctx :
            Context variable to be used by the object creator. Not used by the
            modbus module.

    Raises:

        ModbusClientError: Raised for any general modbus client error.

        ModbusClientTimeoutError: Raised for a modbus client request timeout.

        ModbusClientException: Raised for an exception response to a modbus
            client request.

    Attributes:

        slave_id
            Modbus slave id.

        ctx
            Context variable to be used by the object creator. Not used by the
            modbus module.

        modbus_map
            The :const:`sunspec.core.modbus.mbmap.ModbusMap` instance associated
            with the device.
    """

    def __init__(self, slave_id, name, pathlist=None, max_count=None, ctx=None):

        self.slave_id = slave_id
        self.name = name
        self.ctx = ctx
        self.modbus_map = None

        if name is not None:
            self.modbus_map = mbmap.ModbusMap(slave_id)
            self.modbus_map.from_xml(name, pathlist)
        else:
            raise mbmap.ModbusMapError('No modbus map file provided during initialization')

    def close(self):
        """Close the device. Called when device is not longer in use.
        """

        pass

    def read(self, addr, count, op=None):
        """Read Modbus device registers. If no connection exists to the
        destination, one is created and disconnected at the end of the request.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Read length in Modbus registers.

            op :
                Modbus function code for request.

        Returns:

            Byte string containing register contents.
        """

        if self.modbus_map is not None:
            return self.modbus_map.read(addr, count, op)
        else:
            raise ModbusClientError('No modbus map set for device')

    def write(self, addr, data):
        """Write Modbus device registers. If no connection exists to the
        destination, one is created and disconnected at the end of the request.

        Parameters:

            addr :
                Starting Modbus address.

            count :
                Byte string containing register contents.
        """

        if self.modbus_map is not None:
            return self.modbus_map.write(addr, data)
        else:
            raise ModbusClientError('No modbus map set for device')

def __generate_crc16_table():
    ''' Generates a crc16 lookup table

    .. note:: This will only be generated once
    '''
    result = []
    for byte in range(256):
        crc = 0x0000
        for bit in range(8):
            if (byte ^ crc) & 0x0001:
                crc = (crc >> 1) ^ 0xa001
            else: crc >>= 1
            byte >>= 1
        result.append(crc)
    return result

__crc16_table = __generate_crc16_table()

def computeCRC(data):
    ''' Computes a crc16 on the passed in string. For modbus,
    this is only used on the binary serial protocols (in this
    case RTU).

    The difference between modbus's crc16 and a normal crc16
    is that modbus starts the crc value out at 0xffff.

    :param data: The data to create a crc16 of
    :returns: The calculated CRC
    '''
    crc = 0xffff
    for a in data:
        idx = __crc16_table[(crc ^ a) & 0xff];
        crc = ((crc >> 8) & 0xff) ^ idx
    swapped = ((crc << 8) & 0xff00) | ((crc >> 8) & 0x00ff)
    return swapped

def checkCRC(data, check):
    ''' Checks if the data matches the passed in CRC

    :param data: The data to create a crc16 of
    :param check: The CRC to validate
    :returns: True if matched, False otherwise
    '''
    return computeCRC(data) == check
