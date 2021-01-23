import xml.dom.minidom
from xml.parsers.expat import ExpatError
from argo_egi_connectors.helpers import filename_date, module_class_name
from argo_egi_connectors.io.connection import ConnectorError


class Helpers(object):
    def __init__(self, logger, *args, **kwargs):
        self.logger = logger

    def _parse_xmltext(self, nodelist):
        rc = []
        for node in nodelist:
            if node.nodeType == node.TEXT_NODE:
                rc.append(node.data)
        return ''.join(rc)

    def _parse_scopes(self, xml_node):
        scopes = list()

        for elem in xml_node.childNodes:
            if elem.nodeName == 'SCOPES':
                for subelem in elem.childNodes:
                    if subelem.nodeName == 'SCOPE':
                        scopes.append(subelem.childNodes[0].nodeValue)

        return scopes

    def _parse_xml(self, data):
        try:
            return xml.dom.minidom.parseString(data)

        except ExpatError as exc:
            msg = '{} Customer:{} : Error parsing XML feed - {}'.format(module_class_name(self), self.logger.customer, repr(exc))
            self.logger.error(msg)
            raise ConnectorError()

        except Exception as exc:
            msg = '{} Customer:{} : Error - {}'.format(module_class_name(self), self.logger.customer, repr(exc))
            self.logger.error(msg)
            raise exc


class ParseSites(Helpers):
    def __init__(self, logger, data, custname, uid=False):
        super().__init__(logger)
        self.data = data
        self.uidservtype = uid
        self.custname = custname
        self._sites = dict()
        self._parse_data()

    def _parse_data(self):
        try:
            xml_data = self._parse_xml(self.data)
            sites = xml_data.getElementsByTagName('SITE')
            for site in sites:
                site_name = site.getAttribute('NAME')
                if site_name not in self._sites:
                    self._sites[site_name] = {'site': site_name}
                self._sites[site_name]['infrastructure'] = self._parse_xmltext(site.getElementsByTagName('PRODUCTION_INFRASTRUCTURE')[0].childNodes)
                self._sites[site_name]['certification'] = self._parse_xmltext(site.getElementsByTagName('CERTIFICATION_STATUS')[0].childNodes)
                self._sites[site_name]['ngi'] = self._parse_xmltext(site.getElementsByTagName('ROC')[0].childNodes)
                self._sites[site_name]['scope'] = ', '.join(self._parse_scopes(site))

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as exc:
            logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing - %s' % (logger.customer, logger.job, repr(exc).replace('\'', '').replace('\"', '')))
            raise exc

    def get_group_groups(self):
        group_list, groupofgroups = list(), list()
        group_list = group_list + sorted([value for _, value in self._sites.items()], key=lambda s: s['ngi'])

        for group in group_list:
            tmpg = dict()
            tmpg['type'] = 'NGI'
            tmpg['group'] = group['ngi']
            tmpg['subgroup'] = group['site']
            tmpg['tags'] = {'certification': group['certification'],
                            'scope': group.get('scope', ''),
                            'infrastructure': group['infrastructure']}

            groupofgroups.append(tmpg)

        return groupofgroups


class ParseServiceEndpoints(Helpers):
    def __init__(self, logger, data, custname, uid=False):
        super().__init__(logger)
        self.data = data
        self.uidservtype = uid
        self.custname = custname
        self._service_endpoints = dict()
        self._parse_data()

    def _parse_data(self):
        try:
            xml_data = self._parse_xml(self.data)
            services = xml_data.getElementsByTagName('SERVICE_ENDPOINT')
            for service in services:
                service_id = ''
                if service.getAttributeNode('PRIMARY_KEY'):
                    service_id = str(service.attributes['PRIMARY_KEY'].value)
                if service_id not in self._service_endpoints:
                    self._service_endpoints[service_id] = {}
                self._service_endpoints[service_id]['hostname'] = self._parse_xmltext(service.getElementsByTagName('HOSTNAME')[0].childNodes)
                self._service_endpoints[service_id]['type'] = self._parse_xmltext(service.getElementsByTagName('SERVICE_TYPE')[0].childNodes)
                self._service_endpoints[service_id]['monitored'] = self._parse_xmltext(service.getElementsByTagName('NODE_MONITORED')[0].childNodes)
                self._service_endpoints[service_id]['production'] = self._parse_xmltext(service.getElementsByTagName('IN_PRODUCTION')[0].childNodes)
                self._service_endpoints[service_id]['site'] = self._parse_xmltext(service.getElementsByTagName('SITENAME')[0].childNodes)
                self._service_endpoints[service_id]['roc'] = self._parse_xmltext(service.getElementsByTagName('ROC_NAME')[0].childNodes)
                self._service_endpoints[service_id]['service_id'] = service_id
                self._service_endpoints[service_id]['scope'] = ', '.join(self._parse_scopes(service))
                self._service_endpoints[service_id]['sortId'] = self._service_endpoints[service_id]['hostname'] + '-' + self._service_endpoints[service_id]['type'] + '-' + self._service_endpoints[service_id]['site']

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as exc:
            self.logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing feed - %s' % (self.logger.customer, self.logger.job, repr(exc).replace('\'', '').replace('\"', '')))
            raise exc

    def get_group_endpoints(self):
        group_list, groupofendpoints = list(), list()
        group_list = group_list + sorted([value for _, value in self._service_endpoints.items()], key=lambda s: s['site'])

        for group in group_list:
            tmpg = dict()
            tmpg['type'] = 'SITES'
            tmpg['group'] = group['site']
            tmpg['service'] = group['type']
            if self.uidservtype:
                tmpg['hostname'] = '{1}_{0}'.format(group['service_id'], group['hostname'])
            else:
                tmpg['hostname'] = group['hostname']
            tmpg['tags'] = {'scope': group.get('scope', ''),
                            'monitored': '1' if group['monitored'] == 'Y' or
                            group['monitored'] == 'True' else '0',
                            'production': '1' if group['production'] == 'Y' or
                            group['production'] == 'True' else '0'}
            groupofendpoints.append(tmpg)

        return groupofendpoints


class ParseServiceGroups(Helpers):
    def __init__(self, logger, data, custname, uid=False):
        super().__init__(logger)
        self.data = data
        self.uidservtype = uid
        self.custname = custname
        # group_groups and group_endpoints components for ServiceGroup topology
        self._service_groups = dict()
        self._parse_data()

    def _parse_data(self):
        try:
            xml_data = self._parse_xml(self.data)
            groups = xml_data.getElementsByTagName('SERVICE_GROUP')
            for group in groups:
                group_id = group.getAttribute('PRIMARY_KEY')
                if group_id not in self._service_groups:
                    self._service_groups[group_id] = {}
                self._service_groups[group_id]['name'] = self._parse_xmltext(group.getElementsByTagName('NAME')[0].childNodes)
                self._service_groups[group_id]['monitored'] = self._parse_xmltext(group.getElementsByTagName('MONITORED')[0].childNodes)

                self._service_groups[group_id]['services'] = []
                services = group.getElementsByTagName('SERVICE_ENDPOINT')
                self._service_groups[group_id]['scope'] = ', '.join(self._parse_scopes(group))

                for service in services:
                    tmps = dict()

                    tmps['hostname'] = self._parse_xmltext(service.getElementsByTagName('HOSTNAME')[0].childNodes)
                    try:
                        tmps['service_id'] = self._parse_xmltext(service.getElementsByTagName('PRIMARY_KEY')[0].childNodes)
                    except IndexError:
                        tmps['service_id'] = service.getAttribute('PRIMARY_KEY')
                    tmps['type'] = self._parse_xmltext(service.getElementsByTagName('SERVICE_TYPE')[0].childNodes)
                    tmps['monitored'] = self._parse_xmltext(service.getElementsByTagName('NODE_MONITORED')[0].childNodes)
                    tmps['production'] = self._parse_xmltext(service.getElementsByTagName('IN_PRODUCTION')[0].childNodes)
                    tmps['scope'] = ', '.join(self._parse_scopes(service))
                    self._service_groups[group_id]['services'].append(tmps)

        except (KeyError, IndexError, TypeError, AttributeError, AssertionError) as exc:
            self.logger.error(module_class_name(self) + 'Customer:%s Job:%s : Error parsing feed - %s' % (self.logger.customer, self.logger.job, repr(exc).replace('\'', '').replace('\"', '')))
            raise exc

    def get_group_endpoints(self):
        group_list, groupofendpoints = list(), list()
        group_list = group_list + [value for _, value in self._service_groups.items()]

        for group in group_list:
            for service in group['services']:
                tmpg = dict()
                tmpg['type'] = 'SERVICEGROUPS'
                tmpg['group'] = group['name']
                tmpg['service'] = service['type']
                if self.uidservtype:
                    tmpg['hostname'] = '{1}_{0}'.format(service['service_id'], service['hostname'])
                else:
                    tmpg['hostname'] = service['hostname']
                tmpg['tags'] = {'scope': service.get('scope', ''),
                                'monitored': '1' if service['monitored'].lower() == 'Y'.lower() or
                                service['monitored'].lower() == 'True'.lower() else '0',
                                'production': '1' if service['production'].lower() == 'Y'.lower() or
                                service['production'].lower() == 'True'.lower() else '0'}
                groupofendpoints.append(tmpg)

        return groupofendpoints

    def get_group_groups(self):
        group_list, groupofgroups = list(), list()
        group_list = group_list + [value for _, value in self._service_groups.items()]

        for group in group_list:
            tmpg = dict()
            tmpg['type'] = 'PROJECT'
            tmpg['group'] = self.custname
            tmpg['subgroup'] = group['name']
            tmpg['tags'] = {'monitored': '1' if group['monitored'].lower() == 'Y'.lower() or
                            group['monitored'].lower() == 'True'.lower() else '0', 'scope': group.get('scope', '')}
            groupofgroups.append(tmpg)

        return groupofgroups
