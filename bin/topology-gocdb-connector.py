#!/usr/bin/python3

# Copyright (c) 2013 GRNET S.A., SRCE, IN2P3 CNRS Computing Centre
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS
# IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language
# governing permissions and limitations under the License.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of either GRNET S.A., SRCE or IN2P3 CNRS Computing
# Centre
#
# The work represented by this source file is partially funded by
# the EGI-InSPIRE project through the European Commission's 7th
# Framework Programme (contract # INFSO-RI-261323)

import argparse
import copy
import os
import sys
import re

from argo_egi_connectors import input
from argo_egi_connectors import output
from argo_egi_connectors.log import Logger

from argo_egi_connectors.config import Global, CustomerConf
from argo_egi_connectors.helpers import filename_date, module_class_name, datestamp, date_check
from urllib.parse import urlparse

logger = None

# GOCDB explicitly says &scope='' for all scopes
SERVENDPI = '/gocdbpi/private/?method=get_service_endpoint&scope='
SITESPI = '/gocdbpi/private/?method=get_site&scope='
SERVGROUPPI = '/gocdbpi/private/?method=get_service_group&scope='

fetchtype = ''

globopts = {}
custname = ''

isok = True


class GOCDBReader:
    def __init__(self, feed, paging=False, auth=None):
        self._o = urlparse(feed)

        # group groups and groups components for Sites topology
        self._sites_service_endpoints = dict()
        self._sites = dict()

        # group_groups and group_endpoints components for ServiceGroup topology
        self._service_groups = dict()

        self.fetched = False
        self.state = True
        self.paging = paging
        self.custauth = auth

    def _get_text(self, nodelist):
        rc = []
        for node in nodelist:
            if node.nodeType == node.TEXT_NODE:
                rc.append(node.data)
        return ''.join(rc)

    def getGroupOfServices(self, uidservtype=False):
        if not self.fetched:
            if not self.state or not self.loadDataIfNeeded():
                return []

        groups, gl = list(), list()

        gl = gl + [value for key, value in self._service_groups.items()]

        for d in gl:
            for service in d['services']:
                g = dict()
                g['type'] = fetchtype.upper()
                g['group'] = d['name']
                g['service'] = service['type']
                if uidservtype:
                    g['hostname'] = '{1}_{0}'.format(service['service_id'], service['hostname'])
                else:
                    g['hostname'] = service['hostname']
                g['tags'] = {'scope': service.get('scope', ''),
                             'monitored': '1' if service['monitored'].lower() == 'Y'.lower() or
                             service['monitored'].lower() == 'True'.lower() else '0',
                             'production': '1' if service['production'].lower() == 'Y'.lower() or
                             service['production'].lower() == 'True'.lower() else '0'}
                groups.append(g)

        return groups

    def getGroupOfGroups(self):
        if not self.fetched:
            if not self.state or not self.loadDataIfNeeded():
                return []

        groupofgroups, gl = list(), list()

        gl = gl + [value for key, value in self._service_groups.items()]

        for d in gl:
            g = dict()
            g['type'] = 'PROJECT'
            g['group'] = custname
            g['subgroup'] = d['name']
            g['tags'] = {'monitored': '1' if d['monitored'].lower() == 'Y'.lower() or
                         d['monitored'].lower() == 'True'.lower() else '0', 'scope': d.get('scope', '')}
            groupofgroups.append(g)
        gg = []
        gg = gg + sorted([value for key, value in self._sites.items()], key=lambda s: s['ngi'])

        for gr in gg:
            g = dict()
            g['type'] = 'NGI'
            g['group'] = gr['ngi']
            g['subgroup'] = gr['site']
            g['tags'] = {'certification': gr['certification'],
                         'scope': gr.get('scope', ''),
                         'infrastructure': gr['infrastructure']}

            groupofgroups.append(g)

        return groupofgroups

    def getGroupOfEndpoints(self, uidservtype=False):
        if not self.fetched:
            if not self.state or not self.loadDataIfNeeded():
                return []

        groupofendpoints, ge = list(), list()

        ge = ge + sorted([value for key, value in self._sites_service_endpoints.items()], key=lambda s: s['site'])

        for gr in ge:
            g = dict()
            g['type'] = fetchtype.upper()
            g['group'] = gr['site']
            g['service'] = gr['type']
            if uidservtype:
                g['hostname'] = '{1}_{0}'.format(gr['service_id'], gr['hostname'])
            else:
                g['hostname'] = gr['hostname']
            g['tags'] = {'scope': gr.get('scope', ''),
                         'monitored': '1' if gr['monitored'] == 'Y' or
                         gr['monitored'] == 'True' else '0',
                         'production': '1' if gr['production'] == 'Y' or
                         gr['production'] == 'True' else '0'}
            groupofendpoints.append(g)

        return groupofendpoints

    def loadDataIfNeeded(self):
        try:
            self.getServiceEndpoints(self._sites_service_endpoints)
            self.getServiceGroups(self._service_groups)
            self.getSitesInternal(self._sites)
            self.fetched = True
        except Exception:
            self.state = False
            return False

        return True

    def _get_xmldata(self, pi):
        res = input.connection(logger, module_class_name(self), globopts,
                               self._o.scheme, self._o.netloc, pi, custauth=self.custauth)
        if not res:
            raise input.ConnectorError()

        doc = input.parse_xml(logger, module_class_name(self), globopts, res,
                              self._o.scheme + '://' + self._o.netloc + pi)
        return doc

    def _get_service_endpoints(self, serviceList, doc):
        try:
            services = doc.getElementsByTagName('SERVICE_ENDPOINT')
            for service in services:
                serviceId = ''
                if service.getAttributeNode('PRIMARY_KEY'):
                    serviceId = str(service.attributes['PRIMARY_KEY'].value)
                if serviceId not in serviceList:
                    serviceList[serviceId] = {}
                serviceList[serviceId]['hostname'] = self._get_text(service.getElementsByTagName('HOSTNAME')[0].childNodes)
                serviceList[serviceId]['type'] = self._get_text(service.getElementsByTagName('SERVICE_TYPE')[0].childNodes)
                serviceList[serviceId]['monitored'] = self._get_text(service.getElementsByTagName('NODE_MONITORED')[0].childNodes)
                serviceList[serviceId]['production'] = self._get_text(service.getElementsByTagName('IN_PRODUCTION')[0].childNodes)
                serviceList[serviceId]['site'] = self._get_text(service.getElementsByTagName('SITENAME')[0].childNodes)
                serviceList[serviceId]['roc'] = self._get_text(service.getElementsByTagName('ROC_NAME')[0].childNodes)
                serviceList[serviceId]['service_id'] = serviceId
                serviceList[serviceId]['scope'] = ', '.join(self._get_scopes(service))
                serviceList[serviceId]['sortId'] = serviceList[serviceId]['hostname'] + '-' + serviceList[serviceId]['type'] + '-' + serviceList[serviceId]['site']

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as e:
            logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing feed %s - %s' % (logger.customer, logger.job, self._o.scheme + '://' + self._o.netloc + SERVENDPI,
                                                                                                        repr(e).replace('\'', '').replace('\"', '')))
            raise e

    def getServiceEndpoints(self, serviceList):
        try:
            if self.paging:
                count, cursor = 1, 0
                while count != 0:
                    doc = self._get_xmldata(SERVENDPI + '&next_cursor=' + str(cursor))
                    count = int(doc.getElementsByTagName('count')[0].childNodes[0].data)
                    links = doc.getElementsByTagName('link')
                    for le in links:
                        if le.getAttribute('rel') == 'next':
                            href = le.getAttribute('href')
                            for e in href.split('&'):
                                if 'next_cursor' in e:
                                    cursor = e.split('=')[1]
                    self._get_service_endpoints(serviceList, doc)

            else:
                doc = self._get_xmldata(SERVENDPI)
                self._get_service_endpoints(serviceList, doc)

        except input.ConnectorError as e:
            raise e

        except Exception as e:
            raise e

    def _get_sites_internal(self, siteList, doc):
        try:
            sites = doc.getElementsByTagName('SITE')
            for site in sites:
                siteName = site.getAttribute('NAME')
                if siteName not in siteList:
                    siteList[siteName] = {'site': siteName}
                siteList[siteName]['infrastructure'] = self._get_text(site.getElementsByTagName('PRODUCTION_INFRASTRUCTURE')[0].childNodes)
                siteList[siteName]['certification'] = self._get_text(site.getElementsByTagName('CERTIFICATION_STATUS')[0].childNodes)
                siteList[siteName]['ngi'] = self._get_text(site.getElementsByTagName('ROC')[0].childNodes)
                siteList[siteName]['scope'] = ', '.join(self._get_scopes(site))

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as e:
            logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing feed %s - %s' % (logger.customer, logger.job, self._o.scheme + '://' + self._o.netloc + SITESPI,
                                                                                                        repr(e).replace('\'', '').replace('\"', '')))
            raise e

    def getSitesInternal(self, siteList):
        try:
            if self.paging:
                count, cursor = 1, 0
                while count != 0:
                    doc = self._get_xmldata(SITESPI + '&next_cursor=' + str(cursor))
                    count = int(doc.getElementsByTagName('count')[0].childNodes[0].data)
                    links = doc.getElementsByTagName('link')
                    for le in links:
                        if le.getAttribute('rel') == 'next':
                            href = le.getAttribute('href')
                            for e in href.split('&'):
                                if 'next_cursor' in e:
                                    cursor = e.split('=')[1]
                    self._get_sites_internal(siteList, doc)

            else:
                doc = self._get_xmldata(SITESPI)
                self._get_sites_internal(siteList, doc)

        except input.ConnectorError as e:
            raise e

        except Exception as e:
            raise e

    def _get_scopes(self, xml_node):
        scopes = list()

        for elem in xml_node.childNodes:
            if elem.nodeName == 'SCOPES':
                for subelem in elem.childNodes:
                    if subelem.nodeName == 'SCOPE':
                        scopes.append(subelem.childNodes[0].nodeValue)

        return scopes

    def _get_service_groups(self, groupList, doc):
        try:
            doc = self._get_xmldata(SERVGROUPPI)
            groups = doc.getElementsByTagName('SERVICE_GROUP')
            for group in groups:
                groupId = group.getAttribute('PRIMARY_KEY')
                if groupId not in groupList:
                    groupList[groupId] = {}
                groupList[groupId]['name'] = self._get_text(group.getElementsByTagName('NAME')[0].childNodes)
                groupList[groupId]['monitored'] = self._get_text(group.getElementsByTagName('MONITORED')[0].childNodes)

                groupList[groupId]['services'] = []
                services = group.getElementsByTagName('SERVICE_ENDPOINT')
                groupList[groupId]['scope'] = ', '.join(self._get_scopes(group))

                for service in services:
                    serviceDict = dict()

                    serviceDict['hostname'] = self._get_text(service.getElementsByTagName('HOSTNAME')[0].childNodes)
                    try:
                        serviceDict['service_id'] = self._get_text(service.getElementsByTagName('PRIMARY_KEY')[0].childNodes)
                    except IndexError:
                        serviceDict['service_id'] = service.getAttribute('PRIMARY_KEY')
                    serviceDict['type'] = self._get_text(service.getElementsByTagName('SERVICE_TYPE')[0].childNodes)
                    serviceDict['monitored'] = self._get_text(service.getElementsByTagName('NODE_MONITORED')[0].childNodes)
                    serviceDict['production'] = self._get_text(service.getElementsByTagName('IN_PRODUCTION')[0].childNodes)
                    serviceDict['scope'] = ', '.join(self._get_scopes(service))
                    groupList[groupId]['services'].append(serviceDict)

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as e:
            logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing feed %s - %s' % (logger.customer, logger.job, self._o.scheme + '://' + self._o.netloc + SERVGROUPPI,
                                                                                                        repr(e).replace('\'', '').replace('\"', '')))
            raise e

    def getServiceGroups(self, groupList):
        try:
            if self.paging:
                count, cursor = 1, 0
                while count != 0:
                    doc = self._get_xmldata(SERVGROUPPI + '&next_cursor=' + str(cursor))
                    count = int(doc.getElementsByTagName('count')[0].childNodes[0].data)
                    links = doc.getElementsByTagName('link')
                    for le in links:
                        if le.getAttribute('rel') == 'next':
                            href = le.getAttribute('href')
                            for e in href.split('&'):
                                if 'next_cursor' in e:
                                    cursor = e.split('=')[1]
                    self._get_service_groups(groupList, doc)

            else:
                doc = self._get_xmldata(SERVGROUPPI)
                self._get_service_groups(groupList, doc)

        except input.ConnectorError as e:
            raise e

        except Exception as e:
            raise e


def main():
    global logger, globopts, confcust
    parser = argparse.ArgumentParser(description="""Fetch entities (ServiceGroups, Sites, Endpoints)
                                                    from GOCDB for every customer and job listed in customer.conf and write them
                                                    in an appropriate place""")
    parser.add_argument('-c', dest='custconf', nargs=1, metavar='customer.conf', help='path to customer configuration file', type=str, required=False)
    parser.add_argument('-g', dest='gloconf', nargs=1, metavar='global.conf', help='path to global configuration file', type=str, required=False)
    parser.add_argument('-d', dest='date', metavar='YEAR-MONTH-DAY', help='write data for this date', type=str, required=False)
    args = parser.parse_args()
    group_endpoints, group_groups = [], []
    logger = Logger(os.path.basename(sys.argv[0]))

    fixed_date = None
    if args.date and date_check(args.date):
        fixed_date = args.date

    confpath = args.gloconf[0] if args.gloconf else None
    cglob = Global(sys.argv[0], confpath)
    globopts = cglob.parse()

    confpath = args.custconf[0] if args.custconf else None
    confcust = CustomerConf(sys.argv[0], confpath)
    confcust.parse()
    confcust.make_dirstruct()
    confcust.make_dirstruct(globopts['InputStateSaveDir'.lower()])
    topofeed = confcust.get_topofeed()
    topofeedpaging = confcust.get_topofeedpaging()
    uidservtype = confcust.get_uidserviceendpoints()

    auth_custopts = confcust.get_authopts()
    auth_opts = cglob.merge_opts(auth_custopts, 'authentication')
    auth_complete, missing = cglob.is_complete(auth_opts, 'authentication')
    if auth_complete:
        gocdb = GOCDBReader(topofeed, topofeedpaging, auth=auth_opts)
    else:
        logger.error('%s options incomplete, missing %s' % ('authentication', ' '.join(missing)))
        raise SystemExit(1)

    logger.customer = custname

    webapi_custopts = confcust.get_webapiopts()
    webapi_opts = cglob.merge_opts(webapi_custopts, 'webapi')
    webapi_complete, missopt = cglob.is_complete(webapi_opts, 'webapi')
    if not webapi_complete:
        logger.error('Customer:%s %s options incomplete, missing %s' % (logger.customer, 'webapi', ' '.join(missopt)))
        raise SystemExit(1)

    group_endpoints = gocdb.getGroupOfServices(uidservtype)
    group_endpoints.append(gocdb.getGroupOfEndpoints(uidservtype))
    group_groups = gocdb.getGroupOfGroups()

    if not gocdb.state:
        raise SystemExit(1)

    numge = len(group_endpoints)
    numgg = len(group_groups)

    if eval(globopts['GeneralPublishWebAPI'.lower()]):
        webapi = output.WebAPI(sys.argv[0], webapi_opts['webapihost'],
                               webapi_opts['webapitoken'], logger,
                               int(globopts['ConnectionRetry'.lower()]),
                               int(globopts['ConnectionTimeout'.lower()]),
                               int(globopts['ConnectionSleepRetry'.lower()]))
        webapi.send(group_groups, 'groups')
        webapi.send(group_endpoints, 'endpoints')

    custdir = confcust.get_custdir()
    if eval(globopts['GeneralWriteAvro'.lower()]):
        if fixed_date:
            filename = filename_date(logger, globopts['OutputTopologyGroupOfGroups'.lower()], custdir, fixed_date.replace('-', '_'))
        else:
            filename = filename_date(logger, globopts['OutputTopologyGroupOfGroups'.lower()], custdir)
        avro = output.AvroWriter(globopts['AvroSchemasTopologyGroupOfGroups'.lower()], filename)
        ret, excep = avro.write(group_groups)
        if not ret:
            logger.error('Customer:%s Job:%s : %s' % (logger.customer, logger.job, repr(excep)))
            raise SystemExit(1)

        if fixed_date:
            filename = filename_date(logger, globopts['OutputTopologyGroupOfEndpoints'.lower()], custdir, fixed_date.replace('-', '_'))
        else:
            filename = filename_date(logger, globopts['OutputTopologyGroupOfEndpoints'.lower()], custdir)
        avro = output.AvroWriter(globopts['AvroSchemasTopologyGroupOfEndpoints'.lower()], filename)
        ret, excep = avro.write(group_endpoints)
        if not ret:
            logger.error('Customer:%s: %s' % (logger.customer, repr(excep)))
            raise SystemExit(1)

    logger.info('Customer:' + custname + ' Fetched Endpoints:%d' % (numge) + ' Groups(%s):%d' % (fetchtype, numgg))


if __name__ == '__main__':
    main()
