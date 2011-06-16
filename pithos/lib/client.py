from httplib import HTTPConnection, HTTP
from sys import stdin

import json
import types
import socket
import pithos.api.faults

ERROR_CODES = {304:'Not Modified',
               400:'Bad Request',
               401:'Unauthorized',
               404:'Not Found',
               409:'Conflict',
               411:'Length Required',
               412:'Precondition Failed',
               416:'Range Not Satisfiable',
               422:'Unprocessable Entity',
               503:'Service Unavailable'}

class Fault(Exception):
    def __init__(self, data='', status=None):
        if data == '' and status in ERROR_CODES.keys():
            data = ERROR_CODES[status]
        Exception.__init__(self, data)
        self.data = data
        self.status = status

class Client(object):
    def __init__(self, host, account, api='v1', verbose=False, debug=False):
        """`host` can also include a port, e.g '127.0.0.1:8000'."""
        
        self.host = host
        self.account = account
        self.api = api
        self.verbose = verbose or debug
        self.debug = debug

    def _chunked_transfer(self, path, method='PUT', f=stdin, headers=None,
                          blocksize=1024):
        http = HTTPConnection(self.host)
        
        # write header
        path = '/%s/%s%s' % (self.api, self.account, path)
        http.putrequest(method, path)
        http.putheader('Content-Type', 'application/octet-stream')
        http.putheader('Transfer-Encoding', 'chunked')
        if headers:
            for header,value in headers.items():
                http.putheader(header, value)
        http.endheaders()
        
        # write body
        data = ''
        while True:
            if f.closed:
                break
            block = f.read(blocksize)
            if block == '':
                break
            data = '%s\r\n%s\r\n' % (hex(len(block)), block)
            try:
                http.send(data)
            except:
                #retry
                http.send(data)
        data = '0x0\r\n'
        try:
            http.send(data)
        except:
            #retry
            http.send(data)
        
        # get response
        resp = http.getresponse()
        
        headers = dict(resp.getheaders())
        
        if self.verbose:
            print '%d %s' % (resp.status, resp.reason)
            for key, val in headers.items():
                print '%s: %s' % (key.capitalize(), val)
            print
        
        data = resp.read()
        if self.debug:
            print data
            print
        
        if data:
            assert data[-1] == '\n'
        #remove trailing enter
        data = data and data[:-1] or data
        
        if int(resp.status) in ERROR_CODES.keys():
            raise Fault(data, int(resp.status))
        
        return resp.status, headers, data

    def req(self, method, path, body=None, headers=None, format='text',
            params=None):
        full_path = '/%s/%s%s?format=%s' % (self.api, self.account, path,
                                            format)
        if params:
            for k,v in params.items():
                if v:
                    full_path = '%s&%s=%s' %(full_path, k, v)
        conn = HTTPConnection(self.host)
        
        #encode whitespace
        full_path = full_path.replace(' ', '%20')
        
        kwargs = {}
        kwargs['headers'] = headers or {}
        if not headers or \
        'Transfer-Encoding' not in headers \
        or headers['Transfer-Encoding'] != 'chunked':
            kwargs['headers']['Content-Length'] = len(body) if body else 0
        if body:
            kwargs['body'] = body
            kwargs['headers']['Content-Type'] = 'application/octet-stream'
        #print '****', method, full_path, kwargs
        try:
            conn.request(method, full_path, **kwargs)
        except socket.error, e:
            raise Fault(status=503)
            
        resp = conn.getresponse()
        headers = dict(resp.getheaders())
        
        if self.verbose:
            print '%d %s' % (resp.status, resp.reason)
            for key, val in headers.items():
                print '%s: %s' % (key.capitalize(), val)
            print
        
        data = resp.read()
        if self.debug:
            print data
            print
        
        if data:
            assert data[-1] == '\n'
        #remove trailing enter
        data = data and data[:-1] or data
        
        if int(resp.status) in ERROR_CODES.keys():
            raise Fault(data, int(resp.status))
        
        #print '*',  resp.status, headers, data
        return resp.status, headers, data

    def delete(self, path, format='text'):
        return self.req('DELETE', path, format=format)

    def get(self, path, format='text', headers=None, params=None):
        return self.req('GET', path, headers=headers, format=format,
                        params=params)

    def head(self, path, format='text', params=None):
        return self.req('HEAD', path, format=format, params=params)

    def post(self, path, body=None, format='text', headers=None):
        return self.req('POST', path, body, headers=headers, format=format)

    def put(self, path, body=None, format='text', headers=None):
        return self.req('PUT', path, body, headers=headers, format=format)

    def _list(self, path, detail=False, params=None, headers=None):
        format = 'json' if detail else 'text'
        status, headers, data = self.get(path, format=format, headers=headers,
                                         params=params)
        if detail:
            data = json.loads(data)
        else:
            data = data.strip().split('\n')
        return data

    def _get_metadata(self, path, prefix=None, params=None):
        status, headers, data = self.head(path, params=params)
        prefixlen = prefix and len(prefix) or 0
        meta = {}
        for key, val in headers.items():
            if prefix and not key.startswith(prefix):
                continue
            elif prefix and key.startswith(prefix):
                key = key[prefixlen:]
            meta[key] = val
        return meta

    def _set_metadata(self, path, entity, **meta):
        headers = {}
        for key, val in meta.items():
            http_key = 'X-%s-Meta-%s' %(entity.capitalize(), key.capitalize())
            headers[http_key] = val
        self.post(path, headers=headers)

    # Storage Account Services

    def list_containers(self, detail=False, params=None, headers=None):
        return self._list('', detail, params, headers)

    def account_metadata(self, restricted=False, until=None):
        prefix = restricted and 'x-account-meta-' or None
        params = until and {'until':until} or None
        return self._get_metadata('', prefix, params=params)

    def update_account_metadata(self, **meta):
        self._set_metadata('', 'account', **meta)

    # Storage Container Services

    def list_objects(self, container, detail=False, params=None, headers=None):
        return self._list('/' + container, detail, params, headers)

    def create_container(self, container, headers=None):
        status, header, data = self.put('/' + container, headers=headers)
        if status == 202:
            return False
        elif status != 201:
            raise Fault(data, int(status))
        return True

    def delete_container(self, container):
        self.delete('/' + container)

    def retrieve_container_metadata(self, container, restricted=False,
                                    until=None):
        prefix = restricted and 'x-container-meta-' or None
        params = until and {'until':until} or None
        return self._get_metadata('/%s' % container, prefix, params=params)

    def update_container_metadata(self, container, **meta):
        self._set_metadata('/' + container, 'container', **meta)

    # Storage Object Services

    def retrieve_object(self, container, object, detail=False, headers=None):
        path = '/%s/%s' % (container, object)
        format = 'json' if detail else 'text'
        status, headers, data = self.get(path, format, headers)
        return data

    def create_object(self, container, object, f=stdin, chunked=False,
                      blocksize=1024, headers=None):
        """
        creates an object
        if f is None then creates a zero length object
        if f is stdin or chunked is set then performs chunked transfer 
        """
        path = '/%s/%s' % (container, object)
        if not chunked and f != stdin:
            data = f and f.read() or None
            return self.put(path, data, headers=headers)
        else:
            return self._chunked_transfer(path, 'PUT', f, headers=headers,
                                   blocksize=1024)

    def update_object(self, container, object, f=stdin, chunked=False,
                      blocksize=1024, headers=None):
        if not f:
            return
        path = '/%s/%s' % (container, object)
        if not chunked and f != stdin:
            data = f.read()
            self.post(path, data, headers=headers)
        else:
            self._chunked_transfer(path, 'POST', f, headers=headers,
                                   blocksize=1024)

    def _change_obj_location(self, src_container, src_object, dst_container,
                             dst_object, remove=False):
        path = '/%s/%s' % (dst_container, dst_object)
        headers = {}
        if remove:
            headers['X-Move-From'] = '/%s/%s' % (src_container, src_object)
        else:
            headers['X-Copy-From'] = '/%s/%s' % (src_container, src_object)
        headers['Content-Length'] = 0
        self.put(path, headers=headers)

    def copy_object(self, src_container, src_object, dst_container,
                             dst_object):
        self._change_obj_location(src_container, src_object,
                                   dst_container, dst_object)

    def move_object(self, src_container, src_object, dst_container,
                             dst_object):
        self._change_obj_location(src_container, src_object,
                                   dst_container, dst_object, True)

    def delete_object(self, container, object):
        self.delete('/%s/%s' % (container, object))

    def retrieve_object_metadata(self, container, object, restricted=False):
        path = '/%s/%s' % (container, object)
        prefix = restricted and 'x-object-meta-' or None
        return self._get_metadata(path, prefix)

    def update_object_metadata(self, container, object, **meta):
        path = '/%s/%s' % (container, object)
        self._set_metadata(path, 'object', **meta)
