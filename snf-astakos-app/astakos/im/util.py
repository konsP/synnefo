# Copyright 2011-2012 GRNET S.A. All rights reserved.
# 
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
# 
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
# 
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
# 
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

import logging
import datetime
import time

from urllib import quote
from urlparse import urlsplit, urlunsplit
from functools import wraps

from datetime import tzinfo, timedelta
from django.http import HttpResponse, urlencode
from django.template import RequestContext
from django.contrib.sites.models import Site
from django.utils.translation import ugettext as _
from django.contrib.auth import login, authenticate
from django.core.urlresolvers import reverse

from astakos.im.models import AstakosUser, Invitation, ApprovalTerms
from astakos.im.settings import INVITATIONS_PER_LEVEL, COOKIE_NAME, COOKIE_DOMAIN, COOKIE_SECURE, FORCE_PROFILE_UPDATE

logger = logging.getLogger(__name__)

class UTC(tzinfo):
   def utcoffset(self, dt):
       return timedelta(0)

   def tzname(self, dt):
       return 'UTC'

   def dst(self, dt):
       return timedelta(0)

def isoformat(d):
   """Return an ISO8601 date string that includes a timezone."""

   return d.replace(tzinfo=UTC()).isoformat()

def epoch(datetime):
    return int(time.mktime(datetime.timetuple())*1000)

def get_or_create_user(email, realname='', first_name='', last_name='', affiliation='', level=0, provider='local', password=''):
    """Find or register a user into the internal database
       and issue a token for subsequent requests.
    """
    user, created = AstakosUser.objects.get_or_create(email=email,
        defaults={
            'password':password,
            'affiliation':affiliation,
            'level':level,
            'invitations':INVITATIONS_PER_LEVEL.get(level, 0),
            'provider':provider,
            'realname':realname,
            'first_name':first_name,
            'last_name':last_name
        })
    if created:
        user.renew_token()
        user.save()
        logger.info('Created user %s', user)
    
    return user

def get_context(request, extra_context={}, **kwargs):
    if not extra_context:
        extra_context = {}
    extra_context.update(kwargs)
    return RequestContext(request, extra_context)

def get_invitation(request):
    """
    Returns the invitation identified by the ``code``.
    
    Raises Invitation.DoesNotExist and Exception if the invitation is consumed
    """
    code = request.GET.get('code')
    if request.method == 'POST':
        code = request.POST.get('code')
    #if not code:
    #    if 'invitation_code' in request.session:
    #        code = request.session.pop('invitation_code')
    if not code:
        return
    invitation = Invitation.objects.get(code = code)
    if invitation.is_consumed:
        raise ValueError(_('Invitation is used'))
    try:
        AstakosUser.objects.get(email = invitation.username)
        raise ValueError(_('Email: %s is reserved' % invitation.username))
    except AstakosUser.DoesNotExist:
        pass
    return invitation

def prepare_response(request, user, next='', renew=False):
    """Return the unique username and the token
       as 'X-Auth-User' and 'X-Auth-Token' headers,
       or redirect to the URL provided in 'next'
       with the 'user' and 'token' as parameters.
       
       Reissue the token even if it has not yet
       expired, if the 'renew' parameter is present
       or user has not a valid token.
    """
    renew = renew or (not user.auth_token)
    renew = renew or (user.auth_token_expires and user.auth_token_expires < datetime.datetime.now())
    if renew:
        user.renew_token()
        user.save()
    
    if FORCE_PROFILE_UPDATE and not user.is_verified and not user.is_superuser:
        params = ''
        if next:
            params = '?' + urlencode({'next': next})
        next = reverse('astakos.im.views.edit_profile') + params
    
    response = HttpResponse()
    
    # authenticate before login
    user = authenticate(email=user.email, auth_token=user.auth_token)
    login(request, user)
    set_cookie(response, user)
    
    if not next:
        next = reverse('astakos.im.views.index')
    
    response['Location'] = next
    response.status_code = 302
    return response

def set_cookie(response, user):
    expire_fmt = user.auth_token_expires.strftime('%a, %d-%b-%Y %H:%M:%S %Z')
    cookie_value = quote(user.email + '|' + user.auth_token)
    response.set_cookie(COOKIE_NAME, value=cookie_value,
                        expires=expire_fmt, path='/',
                        domain=COOKIE_DOMAIN, secure=COOKIE_SECURE)

class lazy_string(object):
    def __init__(self, function, *args, **kwargs):
        self.function=function
        self.args=args
        self.kwargs=kwargs
        
    def __str__(self):
        if not hasattr(self, 'str'):
            self.str=self.function(*self.args, **self.kwargs)
        return self.str

def reverse_lazy(*args, **kwargs):
    return lazy_string(reverse, *args, **kwargs)

def get_latest_terms():
    try:
        term = ApprovalTerms.objects.order_by('-id')[0]
        return term
    except IndexError:
        pass
    return None

def has_signed_terms(user):
    term = get_latest_terms()
    if not term:
        return True
    if not user.has_signed_terms:
        return False
    if not user.date_signed_terms:
        return False
    if user.date_signed_terms < term.date:
        user.has_signed_terms = False
        user.save()
        return False
    return True