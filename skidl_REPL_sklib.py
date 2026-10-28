from collections import defaultdict
from skidl import Pin, Part, Alias, SchLib, SKIDL, TEMPLATE

from skidl.pin import pin_types

SKIDL_lib_version = '0.0.1'

skidl_REPL = SchLib(tool=SKIDL).add_parts(*[
        Part(**{ 'name':'T_DRIVER_CTRL', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_DRIVER_CTRL'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOIC-8', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='VIN',func=pin_types.UNSPEC),
            Pin(num='2',name='GND',func=pin_types.UNSPEC),
            Pin(num='3',name='EN',func=pin_types.UNSPEC),
            Pin(num='4',name='SW',func=pin_types.UNSPEC),
            Pin(num='5',name='FB',func=pin_types.UNSPEC),
            Pin(num='6',name='TEMP',func=pin_types.UNSPEC),
            Pin(num='7',name='LED_IND',func=pin_types.UNSPEC),
            Pin(num='8',name='LED_OUT',func=pin_types.UNSPEC),
            Pin(num='9',name='I_SENSE',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_MOSFET', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_MOSFET'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOT-23', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='G',func=pin_types.UNSPEC),
            Pin(num='2',name='D',func=pin_types.UNSPEC),
            Pin(num='3',name='S',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_DIODE', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_DIODE'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOD-123', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='A',func=pin_types.UNSPEC),
            Pin(num='2',name='K',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_2P', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_2P'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'0805', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='1',func=pin_types.UNSPEC),
            Pin(num='2',name='2',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_BAT', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_BAT'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'BatteryHolder_18650', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='+',func=pin_types.UNSPEC),
            Pin(num='2',name='-',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_NFET', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_NFET'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOT-23', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='G',func=pin_types.UNSPEC),
            Pin(num='2',name='D',func=pin_types.UNSPEC),
            Pin(num='3',name='S',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_PROT', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_PROT'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOT-23-4', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='IN',func=pin_types.UNSPEC),
            Pin(num='2',name='OUT',func=pin_types.UNSPEC),
            Pin(num='3',name='GND',func=pin_types.UNSPEC),
            Pin(num='4',name='EN',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_CAP', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_CAP'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'1210', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='1',func=pin_types.UNSPEC),
            Pin(num='2',name='2',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_LED', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_LED'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'0805', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='A',func=pin_types.UNSPEC),
            Pin(num='2',name='K',func=pin_types.UNSPEC)] }),
        Part(**{ 'name':'T_CHG_IC', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'T_CHG_IC'}), 'ref_prefix':'U', 'fplist':None, 'footprint':'SOT-23-4', 'keywords':None, 'description':'', 'datasheet':None, 'pins':[
            Pin(num='1',name='VIN',func=pin_types.UNSPEC),
            Pin(num='2',name='GND',func=pin_types.UNSPEC),
            Pin(num='3',name='BAT',func=pin_types.UNSPEC),
            Pin(num='4',name='STAT',func=pin_types.UNSPEC)] })])