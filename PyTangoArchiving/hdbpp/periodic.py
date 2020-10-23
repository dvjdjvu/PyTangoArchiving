import fandango as fn
from fandango.objects import SingletonMap, Cached
from fandango.tango import *
from .config import HDBppDB

class HDBppPeriodic(HDBppDB):
    
    @Cached(depth=1000,expire=60.)
    def get_attribute_archiver(self,attribute):
        r = HDBppDB.get_attribute_archiver(self,attribute)
        if not r:
            r = self.get_periodic_attribute_archiver(attribute)
        return r
    
    def add_periodic_archiver(self,server,device,properties={}):
        klass = 'PyHdbppPeriodicArchiver'
        if '/' not in server: 
            server = klass+'/'+server
        fn.tango.add_new_device(server,klass,fn.tango.get_normal_name(device))
        device = fn.tango.get_full_name(device,fqdn=True)
        properties.update({'ConfigurationManagerDS':self.manager})
        fn.tango.put_device_property(device,properties)
        archivers = self.get_periodic_archivers()
        archivers.append(device)
        fn.tango.put_device_property(self.manager,
            'PeriodicArchivers',archivers)
      
    def get_periodic_archivers(self):
        #archs = fn.tango.get_class_devices('PyHdbppPeriodicArchiver')
        archivers = fn.tango.get_device_property(
            self.manager,'PeriodicArchivers')
        if not fn.isSequence(archivers):
            archivers = fn.toList(archivers)
        try:
            return sorted(archivers)
        except:
            return []
            
    @Cached(expire=10.)
    def get_periodic_archivers_attributes(self,regexp='*'):
        #archs = fn.tango.get_class_devices('PyHdbppPeriodicArchiver')
        archivers = dict.fromkeys([a for a in self.get_periodic_archivers() 
                     if fn.clmatch(regexp,a)])
        for a in archivers:
            archivers[a.lower()] = self.get_periodic_archiver_attributes(a)
        return archivers

    @Cached(expire=10.)    
    def get_periodic_archiver_attributes(self,archiver):
        prop = fn.toList(fn.tango.get_device_property(archiver,'AttributeList'))
        return dict(p.lower().split(';',1) for p in prop if p.strip())
        
    
    @Cached(expire=10.)
    def get_periodic_attribute_archiver(self,attribute):
        attribute = fn.tango.get_full_name(attribute,fqdn=True)
        archivers = self.get_periodic_archivers_attributes()
        for a,v in archivers.items():
            if fn.inCl(attribute,v):
                return a
        return ''
    
    @Cached(depth=10000,expire=60.)
    def get_periodic_attribute_period(self,attribute):
        attribute = fn.tango.get_full_name(attribute,fqdn=True).lower()
        archivers = self.get_periodic_archivers_attributes()
        for a,v in archivers.items():
            if attribute in v:
                return self.get_periodic_archiver_periods(a).get(attribute,0)
        return 0
    
    @Cached(depth=100,expire=60.)
    def get_periodic_archiver_periods(self,archiver):
        prop = fn.tango.get_device_property(archiver,'AttributeList')
        periods = dict()
        for p in prop:
            p = p.lower().split(';')
            period = [k for k in p if 'period' in k]
            if not period: continue
            period = float(period[0].split('=')[-1].strip()) if period else 0
            periods[p[0]] = period
        return periods
    
    is_periodic_archived = get_periodic_attribute_archiver
    
    @Cached(expire=10.)
    def get_periodic_attributes(self):
        self.periodic_attributes = {}
        for v in self.get_periodic_archivers_attributes().values():
            for k,p in v.items():
                try:
                    p = [s.lower() for s in p.split(';') if 'period' in s][0]
                    self.periodic_attributes[k.lower()] = int(p.split('=')[-1])
                except:
                    print(fn.except2str())
        return self.periodic_attributes
    
    @Cached(depth=10,expire=60.)
    def get_archived_attributes(self,search='',periodic=True):
        """
        It gets attributes currently assigned to archiver and updates
        internal attribute/archiver index.
        
        DONT USE Manager.AttributeSearch, it is limited to 1024 attrs!
        """
        #print('get_archived_attributes(%s)'%str(search))
        attrs = HDBppDB.get_subscribed_attributes(self, search)
        if periodic:
            attrs.extend(self.get_periodic_attributes())
        return sorted(set(fn.tango.get_full_name(a,fqdn=True).lower()
                          for a in attrs))
    
    def get_next_periodic_archiver(self, attrexp=''):
        """
        attrexp can be used to get archivers already archiving attributes
        """
        loads = self.get_periodic_archivers_attributes()
                
        if attrexp:
            attrs = [a for a in self.get_periodic_attributes()
                        if fn.clmatch(attrexp,a)]
            archs = [self.get_periodic_attribute_archiver(a) for a in attrs]
            if archs:
                loads = dict((k,v) for k,v in loads.items() if k in archs)

        loads = sorted((len(v),k) for k,v in loads.items())
        return loads[0][-1]    
    
    def add_periodic_attribute(self,attribute,period,archiver=None,wait=3.):
        
        attribute = parse_tango_model(attribute,fqdn=True).fullname.lower()
        
        arch = self.get_periodic_attribute_archiver(attribute)
        if arch:
            print('%s is already archived by %s!' % (attribute,arch))
            p = self.get_periodic_attribute_period(attribute)
            if p == period:
                return False
            else:
                archiver = arch
        
        archiver = archiver or self.get_next_periodic_archiver(
                            attrexp = fn.tango.get_dev_name(attribute)+'/*')
            
        if not self.is_attribute_archived(attribute):
            self.info('Attribute %s does not exist in %s database, adding it'
                      % (attribute, self.db_name))
            self.add_attribute(attribute,code_event=True)

        self.info('%s.AttributeAdd(%s,%s)' % (archiver,attribute,period))            
        dp = fn.get_device(archiver,keep=True)
        dp.set_timeout_millis(30000)
        v = dp.AttributeAdd([attribute,str(int(float(period)))])
        fn.wait(wait)
        return v
    
    def add_periodic_attributes(self,attributes,periods,wait=3.):
        """
        attributes must be a list, periods a number, list or dict
        """
        attributes = sorted(parse_tango_model(a,fqdn=True).fullname.lower()
                      for a in attributes)
        if fn.isNumber(periods):
            periods = dict((a,periods) for a in attributes)
        elif fn.isSequence(periods):
            periods = dict(zip(attributes,periods))

        devs = fn.defaultdict(list)
        [devs[fn.tango.get_dev_name(a)].append(a) for a in attributes]
        done = []
        
        for dev,attrs in devs.items():
            archiver = self.get_next_periodic_archiver(attrexp = dev+'/*')
            for attribute in attrs:
                try:
                    period = periods[attribute]
                    self.info('add_periodic_attribute(%s,%s,%s)'
                              % (attribute,period,archiver))
                    self.add_periodic_attribute(attribute,period=period,
                                            archiver=archiver,wait=wait)
                    done.append((attribute,period,archiver))
                except:
                    self.warning(fn.except2str())
                
        return done

    def stop_periodic_archiving(self, attribute):
        try:
            attribute = parse_tango_model(attribute, fqdn=True).fullname.lower()
            arch = self.get_periodic_attribute_archiver(attribute)
            if not arch:
                self.warning('%s is not archived!' % attribute)
            else:
                self.info('Removing %s from %s' % (attribute, arch))
                dp = fn.get_device(archiver)
                v = dp.AttributeRemove([attribute, str(int(float(period)))])
                dp.UpdateAttributeList()
                fn.wait(wait)
                return v
        except:
            self.warning('stop_periodic_archiving(%s) failed!' %
                         (attribute, traceback.format_exc()))
            
    def restart_periodic_archiving(self, attribute):
        try:
            attribute = parse_tango_model(attribute, fqdn=True).fullname.lower()
            arch = self.get_periodic_attribute_archiver(attribute)
            if not arch:
                self.warning('%s is not archived!' % attribute)
            else:
                self.info('Restarting %s at %s' % (attribute, arch))
                dp = fn.get_device(archiver)
                v = dp.AttributeStop(attribute)
                dp.ResetErrorAttributes()
                fn.wait(wait)
                v = dp.AttributeStart(attribute)
                return v
        except:
            self.warning('restart_periodic_archiving(%s) failed!' %
                         (attribute, traceback.format_exc()))        

    def clear_periodic_caches(self):
        self.get_periodic_archiver_attributes.cache.clear()
        self.get_periodic_archivers_attributes.cache.clear()
        self.get_periodic_attribute_archiver.cache.clear()
        self.get_periodic_attribute_period.cache.clear()
