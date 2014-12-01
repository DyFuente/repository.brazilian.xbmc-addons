# -*- coding: UTF-8 -*-
'''
Globo API for plugin.video.globo.com


This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''
import re

import backends
import scraper
import util

# url masks
BASE_URL = 'http://%s.globo.com'

GLOBOTV_URL = BASE_URL % 'globotv'
GLOBOTV_MAIS_URL = GLOBOTV_URL + '/mais/'
GLOBOTV_EPS_JSON = GLOBOTV_URL + '/rede-globo/%s/integras/recentes/%d.json'
GLOBOTV_SHOWTB_URL = 'http://s01.video.glbimg.com/x360/%s.jpg'
GLOBOTV_PROGIMG_URL = 'http://s.glbimg.com/vi/mk/program/%s/logotipo/2/149x84.png'

GLOBOSAT_URL = BASE_URL % 'globosatplay'
GLOBOSAT_LIVE_JSON = GLOBOSAT_URL + '/xhr/transmissoes/ao-vivo.json'

# RAIL_URL = SHOW_URL + '/_/trilhos/%(rail)s/page/%(page)s/'
INFO_URL = 'http://api.globovideos.com/videos/%s/playlist'
HASH_URL = ('http://security.video.globo.com/videos/%s/hash?'
            + 'resource_id=%s&version=%s&player=flash')
LOGIN_URL = 'https://login.globo.com/login/151?tam=widget'
JSAPI_URL = 'http://s.videos.globo.com/p2/j/api.min.js'


class GloboApi(object):

    def __init__(self, plugin):
        self.plugin = plugin
        self.index = plugin.get_storage('index')
        if not isinstance(self.index.get('favorites'), set):
            # needed for v1.3
            # TBD: remove in the future
            self.index.clear()
        if not any(self.index.items()):
            # kick start index for first runs
            self.index.update(self._build_index())
        self.index.sync()
        self.favorites = self.index['favorites']

    def _clear_index(self):
        self.plugin.log.debug('clearing data')
        self.index.clear()
        self.index.sync()
        self.plugin.log.debug('data cleared')

    def _get_hashes(self, video_id, resource_ids, auth_retry=False, player_retry=False):
        playerVersion = self.plugin.get_setting('player_version')

        video_data = self._get_video_info(video_id)
        provider = ('globo' if video_data['channel_id'] == 196
                    else self.plugin.get_setting('play_provider').lower().replace(' ', '_'))
        credentials = self.authenticate(provider)

        args = (video_id, '|'.join(resource_ids), playerVersion)
        data = scraper.get_page(HASH_URL % args, cookies=credentials)

        self.plugin.log.debug('hash requested: %s' % (HASH_URL % args))
        self.plugin.log.debug('resource ids: %s' % '|'.join(resource_ids))
        self.plugin.log.debug('return: %s' % repr(data).encode('ascii', 'replace'))
        try:
            return data['hash']
        except ValueError:
            msg = 'JSON not returned. Message returned:\n%s' % data
            self.plugin.log.error(msg)
            raise
        except KeyError:
            args = (data['http_status_code'], data['message'])
            self.plugin.log.error('request error: [%s] %s' % args)

            if data['message'] == 'Player not recognized':
                # If a 'Player not recognized' message is received, it is
                # either because the player version is not yet set, or it's
                # outdated. In either case, player version is reset and hash
                # computation retried once
                self.plugin.log.debug('reset player version')
                if not player_retry:
                    playerVersion = scraper.get_player_version()
                    self.plugin.set_setting('player_version', playerVersion)
                    self.plugin.log.debug('retrying with new player version %s' % playerVersion)
                    return self._get_hashes(video_id, resource_ids, auth_retry, True)

            if str(args[0]) == '403' and any(credentials.values()):
                # If a 403 is returned (authentication needed) and there is an
                # globo id, then this might be due to session expiration and a
                # retry with a blank id shall be tried
                self.plugin.log.debug('cleaning credentials')
                credentials_key = '%s_credentials' % ('globo' if 'globo' == provider else 'play')
                self.plugin.set_setting(credentials_key, '')
                if not auth_retry:
                    self.plugin.log.debug('retrying authentication')
                    return self._get_hashes(video_id, resource_ids, True, player_retry)
            raise Exception(data['message'])

    # @util.cacheFunction
    def _get_video_info(self, video_id):
        # get video info
        data = scraper.get_page(INFO_URL % video_id)['videos'][0]
        if 'date' not in data:
            # original date is not part of INFO_URLs metadata response
            data['date'] = util.time_format()
        if 'duration' not in data:
            data['duration'] = sum(x['resources'][0]['duration']/1000
                                   for x in data.get('children') or [data])
        return data

    def _build_index(self):
        # get gplay channels
        channels, live = scraper.get_gplay_channels()
        # add globo
        channels.update({
            'globo': ('Rede Globo', 'http://s.glbimg.com/vi/mk/channel/196/logotipo/4/149x84.png'),
        })

        return {
            'index': [
                ('channels', self.plugin.get_string(30011)),
                ('live', self.plugin.get_string(30012)),
                ('favorites', self.plugin.get_string(30013)),
            ],
            'channels': channels,
            'live': live,
            'favorites': set(),
        }

    def _build_globo(self, channel=None):
        categories, shows = scraper.get_globo_shows()
        data = { 'globo': {} }
        for cat, show_list in zip(categories, shows):
            slug = util.slugify(cat)
            data['globo'].update({slug: (cat, None)})
            data[slug] = show_list
        return data

    def _build_globosat(self, channel, show=None):
        shows = scraper.get_gplay_shows(channel)
        pos_show = {
            'megapix': 3,
            'combate': 2
        }
        try:
            pos = pos_show[channel]
        except:
            pos = 2
        data = {
            channel: dict([(slug.split('/')[pos], (name, img))
                           for slug, name, img in shows]) }
        return data

    def authenticate(self, provider):
        try:
            backend = getattr(backends, provider)(self.plugin)
        except AttributeError:
            self.plugin.log.error('%s provider unavailable' % provider)
            self.plugin.notify(self.plugin.get_string(32001) % provider)
        return backend.authenticate()

    def get_path(self, key):
        data = self.index.get(key)
        if data == None:
            method = '_build_%s' % (key if key == 'globo' else 'globosat')
            data = getattr(self, method)(key)
            self.index.update(data)
            data = self.index.get(key)
        return data

    def get_episodes(self, channel, show, page):
        # import pydevd; pydevd.settrace()
        # page_size = int(self.plugin.get_setting('page_size') or 10)
        self.plugin.log.debug('getting episodes for %s/%s, page %s' % (channel, show, page))
        # define scraper method
        method_strs = {
            'megapix': 'get_megapix_episodes',
            'globo':'get_globo_episodes',
        }
        try:
            method = method_strs[channel]
        except:
            method = 'get_gplay_episodes'
        episodes, next = getattr(scraper, method)(channel, show, page)

        return util.struct({'list': episodes, 'next': next})

    def get_videos(self, video_id):
        data = self._get_video_info(video_id)
        if 'children' in data:
            items = [util.struct(self._get_video_info(video['id']))
                     for video in data.get('children')]
        else:
            items = [util.struct(data)]
        return items

    def resolve_video_url(self, video_id):
        # which index to look in the list
        heights = [360, 480, 720]
        video_quality = int(self.plugin.get_setting('video_quality') or 0)
        # get video info
        data = self._get_video_info(video_id)
        self.plugin.log.debug('resolving video: %s' % video_id)
        # this method assumes there's no children
        if 'children' in data:
            raise Exception('Invalid video id: %s' % video_id)
        # build resources dict based on height
        resources = dict((d['height'], d) for d in data['resources']
                         if 'players' in d and 'flash' in d['players'])
        # get resource based on video quality setting
        while True:
            try:
                r = resources[heights[video_quality]]
                break
            except:
                video_quality -= 1
        # get hashes
        hashes = self._get_hashes(video_id, [r['_id']])
        signed_hashes = util.get_signed_hashes(hashes)
        query_string = re.sub(r'{{([a-z]*)}}',
                              r'%(\1)s',
                              r['query_string_template']) % {
                                'hash': signed_hashes[0],
                                'key': 'html5'
                              }
        # build resolved url
        url = '?'.join([r['url'], query_string])
        self.plugin.log.debug('video url: %s' % url)
        return url
