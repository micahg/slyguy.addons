import uuid
from time import time
from xml.dom.minidom import parseString

from slyguy import userdata, mem_cache, settings
from slyguy.session import Session
from slyguy.exceptions import Error
from slyguy.util import hash_6, get_system_arch
from slyguy.log import log

from .language import _

class APIError(Error):
    pass

class API(object):
    def new_session(self, config):
        self.logged_in = False
        self._config = config
        if self._config.loaded:
            self._session = Session(base_url=self._config.api_url, headers=self._config.headers)
            self._set_authentication()

    def _set_authentication(self):
        auth_cookies = userdata.get('auth_cookies')
        if not auth_cookies:
            return

        self._session.cookies.update(auth_cookies)
        self.logged_in = True

    def _device_id(self):
        device_id = userdata.get('device_id')
        if device_id:
            return device_id

        device_id = settings.get('device_id')

        try:
            mac_address = uuid.getnode()
            if mac_address != uuid.getnode():
                mac_address = ''
        except:
            mac_address = ''

        system, arch = get_system_arch()
        device_id = device_id.format(username=userdata.get('username'), mac_address=mac_address, system=system).strip()

        if not device_id:
            device_id = uuid.uuid4()

        log.debug('Raw device id: {}'.format(device_id))
        device_id = hash_6(device_id, length=16)
        log.debug('Hashed device id: {}'.format(device_id))

        userdata.set('device_id', device_id)
        return device_id

    def _refresh_token(self, force=False):
        if not force and userdata.get('expires', 0) > time() or not self.logged_in:
            return

        log.debug('Refreshing token')
        try:
            self._set_profile(userdata.get('profile_id'))
        except Exception as e:
            log.exception(e)
            raise APIError(_.REFRESH_TOKEN_ERROR)
        self._config.refresh()

    def login(self, username, password):
        self.logout()

        payload = {
            'j_password': password,
            'j_username': username,
            'deviceId': self._device_id(),
        }
        resp = self._session.post('/v2.0/androidtv/auth/login.json', params=self._params(), data=payload)
        data = resp.json()

        if not data.get('success'):
            raise APIError(data.get('message'))

        self._save_auth(resp.cookies)
        self._set_profile_data(self.user()['activeProfile'])

    def device_code(self):
        self.logout()
        payload = {'deviceId': self._device_id()}
        return self._session.post('/v2.0/androidtv/ott/auth/code.json', params=self._params(), data=payload).json()

    def device_login(self, code, device_token):
        payload = {
            'activationCode': code,
            'deviceToken': device_token,
            'deviceId': self._device_id(),
        }

        resp = self._session.post('/v2.0/androidtv/ott/auth/status.json', params=self._params(), data=payload)
        data = resp.json()

        if data.get('regenerateCode'):
            return -1
        elif not data.get('success'):
            return False

        self._save_auth(resp.cookies)
        self._set_profile_data(self.user()['activeProfile'])

        return True

    def mvpd_login(self, provider, token):
        self.logout()

        params = {
            'mvpdId': provider['code'],
        }

        headers = {
            'x-auth-suite-token': token,
        }

        data = self._session.post('/v2.0/androidtv/mvpd/authSuite/user/bounded.json', params=self._params(params), headers=headers).json()
        if not data.get('success'):
            raise APIError(data.get('error') or data.get('message'))

        resp = self._session.post('/v2.0/androidtv/mvpd/authSuite/user.json', params=self._params(), headers=headers)
        data = resp.json()
        if not data.get('success'):
            raise APIError(data.get('error') or data.get('message'))

        self._save_auth(resp.cookies)
        self._set_profile_data(self.user()['activeProfile'])

    def _save_auth(self, cookies):
        expires = None
        for cookie in cookies:
            if expires is None or cookie.expires < expires:
                expires = cookie.expires

        userdata.set('expires', min(expires, int(time() + 86400)))
        userdata.set('auth_cookies', cookies.get_dict())

        self._set_authentication()

    def set_profile(self, profile_id):
        self._set_profile(profile_id)
        self._config.refresh()
        mem_cache.empty()

    def _set_profile(self, profile_id):
        resp = self._session.post('/v2.0/androidtv/user/account/profile/switch/{}.json'.format(profile_id), params=self._params())
        data = resp.json()

        if not data.get('success'):
            raise APIError('Failed to set profile: {}'.format(profile_id))

        self._set_profile_data(data['profile'])
        self._save_auth(resp.cookies)

    def _set_profile_data(self, profile):
        userdata.set('profile_id', profile['id'])
        userdata.set('profile_name', profile['name'])
        userdata.set('profile_img', profile['profilePicPath'])

    def _params(self, params=None):
        _params = {'at': self._config.at_token}
        #_params = {'locale': 'en-us', 'at': self._at_token(secret), 'LOCATEMEIN': 'us'}
        if params:
            _params.update(params)
        return _params

    @mem_cache.cached(60*10)
    def carousel(self, url, params=None):
        self._refresh_token()
        params = params or {}
        params.update({
            '_clientRegion': self._config.country_code,
            'start': 0,
        })

        for key in params:
            if type(params[key]) is list:
                params[key] = ','.join(params[key])

        return self._session.get('/v3.0/androidphone{}'.format(url), params=self._params(params)).json()

    @mem_cache.cached(60*10)
    def homegroup(self, id):
        self._refresh_token()
        params = {
            'start': 0,
        }
        return self._session.get('/v3.0/androidphone/homeshowgroup/{}.json'.format(id), params=self._params(params)).json()['homeShowGroupSection']

    @mem_cache.cached(60*10)
    def featured(self):
        self._refresh_token()
        params = {
            'minProximity': 1,
            'minCarouselItems':5,
            'maxCarouselItems': 20,
            'rows': 15,
        }
        return self._session.get('/v3.0/androidphone/home/configurator.json', params=self._params(params)).json()['config']

    @mem_cache.cached(60*10)
    def trending_movies(self):
        self._refresh_token()
        return self._session.get('/v3.0/androidphone/movies/trending.json', params=self._params()).json()

    @mem_cache.cached(60*10)
    def movies(self, genre=None, num_results=12, page=1):
        self._refresh_token()
        params = {
            'includeTrailerInfo': False,
            'packageCode': 'CBS_ALL_ACCESS_AD_FREE_PACKAGE',
            'platformType': 'androidphone',
            'start': (page-1)*num_results,
            'rows': num_results,
            'includeContentInfo': True,
        }

        if genre:
            params['genre'] = genre

        return self._session.get('/v3.0/androidphone/movies.json', params=self._params(params)).json()

    @mem_cache.cached(60*10)
    def movie_genres(self):
        self._refresh_token()
        return self._session.get('/v3.0/androidphone/movies/genre.json', params=self._params()).json()['genres']

    @mem_cache.cached(60*10)
    def show_groups(self):
        self._refresh_token()
        params = {'includeAllShowGroups': 'true'}
        return self._session.get('/v2.0/androidphone/shows/groups.json', params=self._params(params)).json()['showGroups']

    @mem_cache.cached(60*10)
    def show_group(self, group_id):
        self._refresh_token()
        params = {'includeAllShowGroups': 'true'}
        return self._session.get('/v2.0/androidphone/shows/group/{}.json'.format(group_id), params=self._params(params)).json()['group']

    @mem_cache.cached(60*10)
    def related_shows(self, show_id):
        self._refresh_token()
        return self._session.get('/v2.0/androidphone/shows/{}/related/shows.json'.format(show_id), params=self._params()).json()['relatedShows']

    @mem_cache.cached(60*5)
    def show_menu(self, show_id):
        self._refresh_token()
        return self._session.get('/v3.0/androidphone/shows/{}/menu.json'.format(show_id), params=self._params()).json()['showMenu'][0].get('links', [])

    @mem_cache.cached(60*10)
    def show(self, show_id):
        self._refresh_token()
        return self._session.get('/v3.0/androidphone/shows/{}.json'.format(show_id), params=self._params()).json()

    @mem_cache.cached(60*5)
    def show_config(self, show_id, config):
        self._refresh_token()
        params = {
            'platformType': 'apps',
            'rows': 1,
            'begin': 0,
        }
        sections = self._session.get('/v2.0/androidphone/shows/{}/videos/config/{}.json'.format(show_id, config), params=self._params(params)).json()['videoSectionMetadata']
        for section in sections:
            if section['section_type'] == 'Full Episodes':
                return section

        return sections[-1]

    @mem_cache.cached(60*5)
    def seasons(self, show_id):
        self._refresh_token()
        return self._session.get('/v3.0/androidphone/shows/{}/video/season/availability.json'.format(show_id), params=self._params()).json()['video_available_season']['itemList']

    def episodes(self, section, season=None):
        self._refresh_token()

        params = {
            'rows': 999,
            'begin': 0,
        }

        if season:
            params.update({
                'params': 'seasonNum={}'.format(season),
                'seasonNum': season,
            })

        return self._session.get('/v2.0/androidphone/videos/section/{}.json'.format(section), params=self._params(params)).json()['sectionItems']['itemList']

    @mem_cache.cached(60*10)
    def search(self, query):
        self._refresh_token()
        params = {
            'term': query,
            'termCount': 50,
            'showCanVids': 'true',
        }
        return self._session.get('/v3.0/androidphone/contentsearch/search.json', params=self._params(params)).json()['terms']

    def user(self):
        self._refresh_token()
        return self._session.get('/v3.0/androidtv/login/status.json', params=self._params()).json()

    def play(self, video_id):
        self._refresh_token()

        video_data = self._session.get('/v2.0/androidphone/video/cid/{}.json'.format(video_id), params=self._params()).json()['itemList']

        if not video_data:
            raise APIError('No data found for this content')

        video_data = video_data[0]
        if 'pid' not in video_data:
            raise APIError('Check your subscription is valid')

        params = {
            #'formats': 'mpeg-dash',
            'Tracking': 'true',
            'format': 'SMIL',
            #'sig': '0060cbe3920bcb86969e8c733a9cdcdb203d6e57beae30781c706f63',
        }

        url = self._config.get_link_platform_url(video_data['cmsAccountId'], video_data['pid'])
        resp = self._session.get(url, params=params)

        root = parseString(resp.content)

        videos = root.getElementsByTagName('video')
        if not videos:
            error_msg = ''
            for ref in root.getElementsByTagName('ref'):
                error_msg = ref.getAttribute('abstract')
                if error_msg:
                    break
            raise APIError(_(error_msg))

        url = videos[0].getAttribute('src')
        params = {'contentId': video_id}
        data = self._session.get('/v3.0/androidphone/irdeto-control/session-token.json', params=self._params(params)).json()

        return url, data['url'], data['ls_session'], video_data

    def _ip(self):
        return self._session.get(self._config.ip_url, params=self._params()).json()['ip']

    def live_channels(self):
        if not self._config.has_live_tv:
            return []

        self._refresh_token()
        dma = self.dma()

        params = {
            'start': 0,
            'rows': 30,
            '_clientRegion': self._config.country_code,
            'dma': dma['dma'] if dma else None,
            'showListing': 'true',
        }

        data = self._session.get('/v3.0/androidphone/home/configurator/channels.json', params=self._params(params)).json()

        channels = []
        for row in data['carousel']:
            if row['dma'] and dma:
                row['dma'] = dma['tokenDetails']

            channels.append(row)

        return sorted(channels, key=lambda x: x['displayOrder'])

    def epg(self, channel, page=1, rows=25):
        params = {
            'start': (page-1)*rows,
            'rows': rows,
            '_clientRegion': self._config.country_code,
            'showListing': 'true',
        }

        return self._session.get('/v3.0/androidphone/live/channels/{slug}/listings.json'.format(slug=channel), params=self._params(params)).json()['listing']

    ## Dont cache as channels use short lived dma token
    def dma(self):
        self._refresh_token()

        ip = settings.get('region_ip')
        if not ip or ip == '0.0.0.0':
            ip = self._ip()

        params = {
            'ipaddress': ip,
            'dtp': 8, #controls quality
            'syncBackVersion': '3.0',
            'mvpdId': 'AllAccess',
            'is60FPS': 'true',
            'did': self._device_id(),
        }

        data = self._session.get('/v3.0/androidphone/dma.json', params=self._params(params)).json()
        if not data.get('success'):
            log.warning('Failed to get local CBS channel for IP address ({}). Server message: {}'.format(ip, data.get('message')))
            return None

        return data['dmas'][0]

    def logout(self):
        userdata.delete('profile_img')
        userdata.delete('profile_name')
        userdata.delete('profile_id')
        userdata.delete('auth_cookies')
        userdata.delete('device_id')
        userdata.delete('expires')
        mem_cache.empty()
        self.new_session(self._config)

# https://github.com/matthuisman/slyguy.addons/issues/136
# import base64
# import os
# import pyaes
# import time

# aes_key = '302a6a0d70a7e9b967f91d39fef3e387816e3095925ae4537bce96063311f9c5'
# tv_secret = '6c70b33080758409'

# def at_token():
#    payload = '{}|{}'.format(int(time.time())*1000, tv_secret)
#    key = bytes.fromhex(aes_key)
#    iv = os.urandom(16)
#    encrypter = pyaes.Encrypter(pyaes.AESModeOfOperationCBC(key, iv))
#    ciphertext = encrypter.feed(payload)
#    ciphertext += encrypter.feed()
#    ciphertext = b'\x00\x10' + iv + ciphertext
#    return base64.b64encode(ciphertext).decode('utf8')
