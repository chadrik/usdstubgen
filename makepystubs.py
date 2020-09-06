#!/usr/bin/env python
"""
Generates __init__.pyi files which will live alongside their .py equivalent.

pyi files will be properly detected in pycharm 2017+ and other editors that 
fully support PEP484.
"""

import os
import glob
import re
import json
import sys
import shutil
import subprocess
from multiprocessing import Pool
from collections import defaultdict
from distutils.dir_util import copy_tree

import xml.etree.ElementTree as ET

DEFAULT_NUM_PROCS = 10

# FIXME/TODO:
# - Doxygen is not being run on functions (i.e. non-methods).  this also affects
#   our ability to document python static methods which are derived from these 
#   functions.
# - every object has a __reduce__ method which appears to be there just to raise
#   an error if the object is pickled. the return type should be typing.NoReturn
#   but we don't have a new enough version of typing yet.

pymelpath = os.path.expandvars('$REPO_PATH/python/pymel/')
sys.path.insert(0, pymelpath)


# a python identifier
IDENTIFIER = r'([a-zA-Z_][a-zA-Z0-9_]*)'

# mapping from c++ operators to python special methods
OPERATORS = {
    "operator!=": '__neq__',
    "operator==": '__eq__',
    "operator<": '__lt__',
    "operator<=": '__le__',
    "operator>": '__gt__',
    "operator>=": '__ge__',
    "operator bool": '__nonzero__',
    "operator[]": '__getitem__',
    "operator()": '__call__',
}

# c++ to python type subsitutions

LIST_PROXY_TYPE_MAP = [
    (r'\bSdfSubLayerProxy\b', 'pxr.Sdf.ListProxy_SdfSubLayerTypePolicy'),
    # nameTokenKey
    (r'\bSdfNameOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
    (r'\bSdfNameChildrenOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
    (r'\bSdfPropertyOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
]

TYPE_MAP = [
    (r'\bVtArray<\s*SdfAssetPath\s*>', 'SdfAssetPathArray'),
    (r'\bstd::string\b', 'str'),
    (r'\bstring\b', 'str'),
    (r'\bsize_t\b', 'int'),
    (r'\bchar\b', 'str'),
    (r'\bstd::function<(.+)\((.*)\)>', r'Callable[[\2], \1]'),
    (r'\bstd::vector\b', 'List'),
    (r'\bstd::pair\b', 'Tuple'),
    (r'\bstd::set\b', 'List'),
    (r'\bdouble\b', 'float'),
    (r'\bboost::python::', ''),
    (r'\bvoid\b', 'None'),
    (r'\b' + IDENTIFIER + r'Vector\b', r'List[\1]'),
    (r'\bTfToken\b', 'str'),
    (r'\bVtDictionary\b', 'dict'),
    (r'\bUsdMetadataValueMap\b', 'dict'),
    # strip suffixes
    (r'RefPtr\b', ''),
    (r'Ptr\b', ''),
    (r'ConstHandle\b', ''),
    (r'Const\b', ''),
    (r'Handle\b', ''),
    # simple renames:
    (r'\bSdfBatchNamespaceEdit\b', 'pxr.Sdf.NamespaceEdit'),
    # Sdf mapping types:
    # FIXME: the following substitutions use python identifiers (should they be moved to a separate find/replace list?)
    (r'\bSdfLayerHandleSet\b', 'List[pxr.Sdf.Layer]'),
    (r'\bSdfDictionaryProxy\b', 'pxr.Sdf.MapEditProxy_VtDictionary'),
    (r'\bSdfReferencesProxy\b', 'pxr.Sdf.ReferenceTypePolicy'),
    (r'\bSdfSubLayerProxy\b', 'pxr.Sdf.ListProxy_SdfSubLayerTypePolicy'),
    # pathKey
    (r'\bSdfInheritsProxy\b', 'pxr.Sdf.ListEditorProxy_SdfPathKeyPolicy'),
    (r'\bSdfSpecializesProxy\b', 'pxr.Sdf.ListEditorProxy_SdfPathKeyPolicy'),
    # nameTokenKey
    (r'\bSdfNameOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
    (r'\bSdfNameChildrenOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
    (r'\bSdfPropertyOrderProxy\b', 'pxr.Sdf.ListProxy_SdfNameTokenKeyPolicy'),
    # nameKey
    (r'\bSdfVariantSetNamesProxy\b', 'pxr.Sdf.ListEditorProxy_SdfNameKeyPolicy'),
]

# c++ to python default value subsitutions
DEFAULT_VAL_MAP = [
    (r'\bstd::string\(\)', '""'),
    (r'\bNULL\b', 'None'),
    (r'\bnullptr\b', 'None'),
    (r'\btrue\b', 'True'),
    (r'\bfalse\b', 'False'),
]

# methods which don't follow the rules go here
METHOD_RENAMES = {
    'pxr.Sdf.Path': {
        'GetString': 'pathString',
    }
}

ARRAY_TYPES = {
    'Sdf.AssetPathArray': 'Sdf.AssetPath',
    'Vt.IntervalArray': 'Gf.Interval',
    'Vt.Matrix2dArray': 'Gf.Matrix2d',
    'Vt.Matrix3dArray': 'Gf.Matrix3d',
    'Vt.Matrix2fArray': 'Gf.Matrix2f',
    'Vt.Matrix3fArray': 'Gf.Matrix3f',
    'Vt.Matrix4dArray': 'Gf.Matrix4d',
    'Vt.Matrix4fArray': 'Gf.Matrix4f',
    'Vt.QuaternionArray': 'Gf.Quaternion',
    'Vt.QuatdArray': 'Gf.Quatd',
    'Vt.QuatfArray': 'Gf.Quatf',
    'Vt.QuathArray': 'Gf.Quath',
    'Vt.Range1dArray': 'Gf.Range1d',
    'Vt.Range1fArray': 'Gf.Range1f',
    'Vt.Range2dArray': 'Gf.Range2d',
    'Vt.Range2fArray': 'Gf.Range2f',
    'Vt.Range3dArray': 'Gf.Range3d',
    'Vt.Range3fArray': 'Gf.Range3f',
    'Vt.Rect2iArray': 'Gf.Rect2i',
    'Vt.StringArray': 'str',
    'Vt.TokenArray': 'Tf.Token',
    'Vt.Vec2dArray': 'Gf.Vec2d',
    'Vt.Vec2fArray': 'Gf.Vec2f',
    'Vt.Vec2hArray': 'Gf.Vec2h',
    'Vt.Vec2iArray': 'Gf.Vec2i',
    'Vt.Vec3dArray': 'Gf.Vec3d',
    'Vt.Vec3fArray': 'Gf.Vec3f',
    'Vt.Vec3hArray': 'Gf.Vec3h',
    'Vt.Vec3iArray': 'Gf.Vec3i',
    'Vt.Vec4dArray': 'Gf.Vec4d',
    'Vt.Vec4fArray': 'Gf.Vec4f',
    'Vt.Vec4hArray': 'Gf.Vec4h',
    'Vt.Vec4iArray': 'Gf.Vec4i',
    'Vt.BoolArray': 'bool',
    'Vt.FloatArray': 'float',
    'Vt.Int64Array': 'long',
    'Vt.IntArray': 'int',
    # 'Vt.HalfArray': 'pxr_half::half',
    # 'Vt.UIntArray': 'unsigned int',
    # 'Vt.UInt64Array': 'unsigned int64',
    # 'Vt.UShortArray': 'unsigned short',
    # 'Vt.UCharArray': 'unsigned char',
    # 'Vt.CharArray': 'char',
    # 'Vt.ShortArray': 'short',
    # 'Vt.DoubleArray': 'double',
}

STRIP = {'*', 'const', '&', 'friend', 'constexpr'}


# FIXME: once we get more of these, move them to a jinja-templated json file?
def create_list_proxy(source_info, classname, proxied_type):
    doc = {
        'members': {
            'append': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': proxied_type
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
            'clear': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [],
                        'result': 'None',
                    }
                ]
            },
            'copy': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [],
                        'result': classname,
                    }
                ]
            },
            'count': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': proxied_type
                            },
                        ],
                        'result': 'int',
                    }
                ]
            },
            'index': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': proxied_type
                            },
                        ],
                        'result': 'int',
                    }
                ]
            },
            'insert': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'index',
                                'type': 'int'
                            },
                            {
                                'name': 'value',
                                'type': proxied_type
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
            'remove': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': proxied_type
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
            'replace': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'old',
                                'type': proxied_type
                            },
                            {
                                'name': 'new',
                                'type': proxied_type
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
            'ApplyList': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': 'List[%s]' % proxied_type
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
        }
    }
    return doc


def create_array(source_info, classname, proxied_type):
    proxied_type_input = source_info.add_implicit_unions(proxied_type)

    doc = {
        'members': {
            '__init__': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'value',
                                'type': 'Iterable[%s]' % proxied_type_input
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
            '__iter__': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [],
                        'result': 'Iterator[%s]' % proxied_type
                    }
                ]
            },
            '__getitem__': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'index',
                                'type': 'int'
                            },
                        ],
                        'result': proxied_type,
                    }
                ]
            },
            '__setitem__': {
                'type': 'method',
                'signatures': [
                    {
                        'args': [
                            {
                                'name': 'index',
                                'type': 'int'
                            },
                            {
                                'name': 'value',
                                'type': proxied_type_input
                            },
                        ],
                        'result': 'None',
                    }
                ]
            },
        }
    }
    return doc


def add_list_proxies(source_info, modules):
    """
    Add signature information for ListProxy objects, which only exist in python
    and are not inspectable.
    """
    sdf_doc = modules['pxr']['members']['Sdf']['members']
    for _, subst in TYPE_MAP:
        if subst.startswith('pxr.Sdf.ListProxy'):
            classname = subst.split('.')[-1]
            sdf_doc[classname] = create_list_proxy(source_info, classname, 'str')


def add_arrays(source_info, modules):
    """
    Add signature information for Array objects, which only exist in python
    and are not inspectable.
    """
    for array_type, child_type in ARRAY_TYPES.items():
        if '.' in child_type:
            # we use fully-qualified names which become localized by the stub
            # generator
            child_type = 'pxr.' + child_type
        module, classname = array_type.split('.')
        module_doc = modules['pxr']['members'][module]['members']
        module_doc[classname] = create_array(source_info, classname, child_type)


def expandpath(p):
    """
    Returns
    -------
    str
    """
    return os.path.expanduser(os.path.expandvars(os.path.abspath(p)))


def basename(p):
    return os.path.split(p)[1]


def capitalize(s):
    """
    Returns
    -------
    str
    """
    return s[0].upper() + s[1:]


def strip_pxr_namespace(s):
    """
    Returns
    -------
    str
    """
    if s.startswith('pxr::'):
        return s[5:]
    return s


def text(node):
    """
    Get all the text of an Etree node

    Returns
    -------
    str
    """
    return ''.join(node.itertext())


def maybe_result(parts):
    """
    return if the argument looks like a c++ result

    Returns
    -------
    bool
    """
    return 'const' not in parts and ('*' in parts or '&' in parts)


def should_strip_part(x):
    """
    whether the part looks like a c++ keyword

    Returns
    -------
    bool
    """
    return x in STRIP or x.endswith('_API')


class SourceInfo(object):
    '''
    Helper for converting c++ data to python data, using info parsed from the
    source
    '''
    def __init__(self, srcdir, verbose=False):
        self.srcdir = expandpath(srcdir)
        self._valid_modules = None
        self._implicitly_convertible_types = None
        self.verbose = verbose

    def get_valid_modules(self):
        '''
        get a cached list of modules from the source
        '''
        if self._valid_modules is None:
            macro_dir = os.path.join(self.srcdir, 'cmake/macros')
            if not os.path.exists(macro_dir):
                raise RuntimeError("Cannot find cmake macro directory: %s" % macro_dir)
            sys.path.append(macro_dir)
            import generateDocs
            # FIXME: get this list from the actual python modules, so that we can skip
            # processing xml files that don't relate to python.
            module_dirs = generateDocs._getModules(os.path.join(self.srcdir, 'pxr'))
            self._valid_modules = sorted(
                [capitalize(os.path.basename(x)) for x in module_dirs],
                reverse=True)
        return self._valid_modules

    def get_implicitly_convertible_types(self):
        '''
        inspect the boost-python code to parse the rules for implicitly
        convertible types
        '''
        # FIXME: add module prefixes to all types (Output, Input, Parameter, etc are not prefixed)
        # FIXME: parse other conversions defined using TfPyContainerConversions
        if self._implicitly_convertible_types is None:
            output = subprocess.check_output(
                ['grep', 'implicitly_convertible', '-r',
                 os.path.join(self.srcdir, 'pxr'),
                 '--include=wrap*.cpp'])
            code_reg = re.compile(r'\s+implicitly_convertible<\s*(?P<from>(%s|:)+),\s*(?P<to>(%s|:)+)\s*>\(\)' %
                                  (IDENTIFIER, IDENTIFIER))
            result = defaultdict(set)
            for line in output.split('\n'):
                line = line.strip()
                if line:
                    path, code = line.split(':', 1)
                    if '.template.' in path:
                        # skip jinja templates
                        continue
                    # each line looks like:
                    # 'src/pxr/base/lib/gf/wrapQuatd.cpp:    implicitly_convertible<GfQuatf, GfQuatd>();'
                    m = code_reg.search(code)
                    if m:
                        match = m.groupdict()
                        from_type = match['from']
                        to_type = match['to']
                        if to_type == 'This':
                            parts = path.split(os.path.sep)
                            name = os.path.splitext(parts[-1])[0]
                            assert name.startswith('wrap')
                            to_type = self.to_python_id(capitalize(parts[-2]) + name[4:])
                        to_type = self.convert_typestr(to_type, is_arg=None)[0]
                        from_type = self.convert_typestr(from_type, is_arg=None)[0]
                        result[to_type].add(from_type)
                    elif self.verbose:
                        print "no match", line
            self._implicitly_convertible_types = dict(result)
            print self._implicitly_convertible_types.keys()
        return self._implicitly_convertible_types

    def split_module(self, typestr):
        """
        split the c++ type into module name and object name

        Returns
        -------
        List[str]
        """
        for mod in self.get_valid_modules():
            if typestr.startswith(mod):
                s = typestr[len(mod):]
                if s and (s[0].isupper() or s[0] == '_'):
                    return mod, s
        # if typestr.startswith('_'):
        #     result = split_module(typestr[1:])
        #     if len(result) == 2:
        #         return result
        return [typestr]

    def to_python_id(self, typestr):
        """
        Returns
        -------
        str
        """
        typestr = strip_pxr_namespace(typestr)
        parts = self.split_module(typestr)
        if len(parts) == 1:
            return parts[0]
        else:
            mod = parts[0]
            name = parts[1]
            return 'pxr.' + mod + '.' + name

    def add_implicit_unions(self, typestr):
        """
        wrap the type string in a Union[] if it is in the list of types with known
        implicit conversions.

        Parameters
        ----------
        typestr : str
            fully qualified python type identifier

        Returns
        -------
        str
        """
        others = self.get_implicitly_convertible_types().get(typestr)
        if others is not None:
            return 'Union[%s]' % ', '.join([typestr] + sorted(others))
        else:
            return typestr

    def convert_typestr(self, node, is_arg=True):
        """
        Convert a c++ type string to a python type string

        Returns
        -------
        Tuple[str, bool]
            the new typestring and whether the type appears to be a return value
        """
        if isinstance(node, basestring):
            typestr = node
        else:
            if node is None:
                return None
            typestr = text(node)
        parts = typestr.split()
        is_result = maybe_result(parts)
        parts = [x for x in parts if not should_strip_part(x)]
        typestr = ''.join(parts)
        for pattern, replace in TYPE_MAP:
            typestr = re.sub(pattern, replace, typestr)
        # swap container syntax
        typestr = typestr.replace('<', '[')
        typestr = typestr.replace('>', ']')
        # convert to python identifers
        parts = [self.to_python_id(x) for x in re.split(IDENTIFIER, typestr)]

        # note: None is a valid value for is_arg
        if is_arg is True and not is_result:
            parts = [self.add_implicit_unions(x) for x in parts]

        typestr = ''.join(parts)
        typestr = typestr.replace(',', ', ')
        typestr = typestr.replace('&', '')  # FIXME: leftover shit from std::function
        typestr = typestr.replace('::', '.')
        return typestr, is_result

    def convert_default(self, node):
        if isinstance(node, basestring):
            valuestr = node
        else:
            if node is None:
                return None
            valuestr = text(node)
        for pattern, replace in DEFAULT_VAL_MAP:
            valuestr = re.sub(pattern, replace, valuestr)
        return self.convert_typestr(valuestr, is_arg=False)[0]


def convert_xml(path, srcdir, verbose=False, modules=None):
    """
    Add data from a Doxygen xml file into the modules document.

    Parameters
    ----------
    path : str
    srcdir : str
    verbose : bool
    modules : Optional[Dict]

    Returns
    -------
    Dict[str, Any]
        hierarchy of member data
        e.g.
        {
          'mypackage': {
            'members': {
              'mymodule': {
                'members': {
                  'MyClass': {
                    'members': {
                        'my_func': {
                          'type': 'method',
                          'signatures': [
                            {
                              'args': [
                              {
                                'name': 'foo',
                                'type': 'str'
                               },
                              {
                                'name': 'foo',
                                'type': 'int'
                              },
                              'result: 'bool'
                            }
                          ]}}}}}}}}

    """
    source_info = SourceInfo(srcdir, verbose=verbose is not False)
    base_path = basename(path)

    tree = ET.parse(path)
    root = tree.getroot()
    if modules is None:
        modules = {}

    name_cache = {}

    def is_verbose(cpp_name):
        if isinstance(verbose, basestring):
            return cpp_name == verbose
        else:
            return verbose

    def get_name_parts(cpp_name):
        try:
            parts = name_cache[cpp_name]
        except KeyError:
            dotted_name = source_info.to_python_id(cpp_name)
            # if dotted_name.startswith('_'):
            #     continue
            if '.' not in dotted_name:
                # we deal in fully-qualified names here, and
                # maintenance.stubs.packagestubs() converts them to the local
                # scope
                if is_verbose(cpp_name):
                    print("%s: Failed to convert cpp to valid python location %r -> %r" %
                          (base_path, cpp_name, dotted_name))
                return
            parts = dotted_name.split('.')
            name_cache[cpp_name] = parts
        return parts

    def get_member_doc(parts, doc, member=None):
        '''
        Parameters
        ----------
        parts : List[str]
            python identifiers. e.g. ['pxr', 'Sdf', 'Path']
        doc : Dict
            root module document
        member : Optional[str]
            if this is a class, the method name

        Returns
        -------
        Dict
            the member document
        '''
        if member:
            parts = parts + [member]

        for parent in parts[:-1]:
            parent_doc = doc.setdefault(parent, {})
            doc = parent_doc.setdefault('members', {})
        return doc.setdefault(parts[-1], {})

    for obj in root.iter('compounddef'):
        cpp_name = obj.find('compoundname').text
        if obj.attrib['kind'] == 'class':
            is_class = True
        elif obj.attrib['kind'] == 'file':
            is_class = False
        else:
            continue

        is_obj_verbose = is_verbose(cpp_name)

        for member in obj.iter('memberdef'):
            kind = member.attrib['kind']
            if kind != 'function':
                continue

            func_name = member.find('name').text
            if is_obj_verbose:
                print cpp_name, "member:", repr(func_name)

            name_parts = get_name_parts(cpp_name)

            if name_parts is None:
                continue

            args = []
            results = []
            for i, param in enumerate(member.findall('param')):
                # print arg_type
                arg_type, is_result = source_info.convert_typestr(
                    param.find('type'), is_arg=True)
                default = source_info.convert_default(param.find('defval'))

                declname = param.find('declname')
                if declname is None:
                    arg_name = 'arg%d' % i
                    if default:
                        print ("%s: Could not find name for keyword arg: %s.%s "
                               "(using %r)" %
                               (base_path, cpp_name, func_name, arg_name))
                else:
                    arg_name = declname.text

                if is_result:
                    results.append(arg_type)
                else:
                    arg = {
                        'name': arg_name,
                        'type': arg_type
                    }
                    if default is not None:
                        arg['default'] = default
                    # print '    ', `arg_name`, `type.text`, `arg_type`
                    args.append(arg)

            result_type = source_info.convert_typestr(member.find('type'),
                                                      is_arg=False)[0]
            if results and result_type in ['None', 'bool']:
                # FIXME: check for the case where results > 1
                result_type = results[0]

            if is_class and func_name == cpp_name:
                func_name = '__init__'
                result_type = 'None'
            elif func_name.startswith('operator'):
                # TODO: skip if not a supported python operator?
                func_name = OPERATORS.get(func_name, func_name)

            # this must happen after potential renaming of func_name
            if is_class:
                # a method
                doc = get_member_doc(name_parts, modules, func_name)
            else:
                doc = get_member_doc(name_parts, modules)

            try:
                signatures = doc['signatures']
            except KeyError:
                # first visit. fill in the doc:
                signatures = []
                doc.update({
                    'type': 'method',
                    'signatures': signatures
                })

            sig = {
                'args': args,
                'result': result_type
            }
            if sig not in signatures:
                # it is possible that after converting types that two
                # signatures become the same
                signatures.append(sig)
    return modules


def iter_xml_files(xmldir):
    '''
    iterate over all xml files in `xmldir`
    '''
    for name in sorted(glob.glob(os.path.join(xmldir, '*.xml'))):
        basename = os.path.split(name)[1]
        if basename.startswith('class') or basename.endswith('8h.xml'):
            yield name


def _convert_xml(args):
    # here for multiprocessing
    return convert_xml(*args)


def merge_dict(d1, d2):
    """
    recursively update d2 with values from d1.
    """
    for key, from_val in d1.iteritems():
        # print key, from_val
        if key in d2:
            to_val = d2[key]
            if hasattr(from_val, 'iteritems') and hasattr(to_val, 'iteritems'):
                merge_dict(from_val, to_val)
            else:
                d2[key] = from_val
        else:
            d2[key] = from_val


def process_xml(xmldir, srcdir, processes=DEFAULT_NUM_PROCS, verbose=False):
    '''
    walk through xml files and covert those pertaining to classes

    Returns
    -------
    Dict[str, Any]
        hierarchy of member data
    '''
    if isinstance(verbose, basestring):
        # for now convert_xml does not support controlling verbosity at the
        # member level.
        parts = verbose.split('.')
        if parts[0] == 'pxr':
            parts.pop(0)
        verbose = parts[0]

    print "getting files"
    args = [(xmlfile, srcdir, verbose) for xmlfile in iter_xml_files(xmldir)]
    print "converting in %d processes" % processes
    master = {}
    if processes > 1:
        pool = Pool(processes)
        for d in pool.imap_unordered(_convert_xml, args):
            merge_dict(d, master)
    else:
        for argTuple in args:
            merge_dict(_convert_xml(argTuple), master)

    source_info = SourceInfo(srcdir, verbose=verbose is not False)
    add_list_proxies(source_info, master)
    add_arrays(source_info, master)
    return master


def query_cache(type_data, obj):
    import pprint
    parts = obj.split('.')
    if not parts[0] == 'pxr':
        parts.insert(0, 'pxr')

    for part in parts:
        type_data = type_data[part]
        if 'members' in type_data:
            type_data = type_data['members']
    pprint.pprint(type_data)


def makestubs(outputdir, xmldir, srcdir, force_update=False,
              processes=DEFAULT_NUM_PROCS, verbose=False):
    # delete the build directory
    builddir = os.path.join(outputdir, 'pyi')
    if os.path.exists(builddir):
        shutil.rmtree(builddir)

    # write the json cache
    jsonfile = os.path.join(outputdir, 'usd_python.json')
    if force_update or not os.path.exists(jsonfile):
        if not os.path.exists(outputdir):
            os.makedirs(outputdir)
        print("processing xml data (force update %s)" % ('ON' if force_update else 'OFF'))
        type_data = process_xml(xmldir, srcdir, processes=processes,
                                verbose=verbose)

        print("caching xml data to %s" % jsonfile)
        with open(jsonfile, 'w') as f:
            json.dump(type_data, f, indent=4, sort_keys=True)

    else:
        print("loading cached package data from %s" % jsonfile)
        with open(jsonfile, 'r') as f:
            type_data = json.load(f)

    # create the stubs
    import maintenance.stubs
    # Notes: 
    #  - UsdHdydra causes stubgen to crash
    #  - Usdviewq is pure python, except for _usdviewq.so which contains only a few
    #    utils and is somehow different from the others (doesn't use `Tf.PrepareModule(_usdviewq, locals())`)
    #  - the last bit of the regex hides any modules starting with an underscore.
    #    the contents of these modules is imported into the __init__.py files, so we 
    #    don't need separate stubs for them.
    maintenance.stubs.packagestubs(
        'pxr', outputdir=outputdir,  extensions=('pyi',),
        type_data=type_data,
        skip_module_regex="pxr[.](UsdMaya|UsdHydra|Usdviewq|Tf.testenv|(.*[.]_.*))")

    if isinstance(verbose, basestring):
        query_cache(type_data, verbose)


def touch(path):
    with open(path, 'w'):
        pass


def get_parser():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xmldir', help='Directory in which to find Doxygen '
                        'xml files')
    parser.add_argument('srcdir', help='Directory in which to find USD '
                        'source code')
    parser.add_argument('pkgdir',
                        help='Directory in which to find the pxr python package')
    parser.add_argument('--force-update', '-f', action='store_true',
                        help='Force update json package data')
    parser.add_argument('--builddir', default='.',
                        help='Directory in which to generate pyi files')
    parser.add_argument('--release', action='store_true',
                        help='Copy pyi files from builddir to pkgdir')
    parser.add_argument('--processes', '-p', type=int, default=DEFAULT_NUM_PROCS,
                        help='Number of simulataneous processes to run')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enabled verbose output')
    parser.add_argument('--query', '-q', type=str,
                        help='Query the cache. Overrides other behavior')
    return parser


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = get_parser()
    args = parser.parse_args(argv)
    # note: srcdir is expanded later, by SourceInfo (makes it easier to test)
    builddir = expandpath(args.builddir)
    pkgdir = expandpath(args.pkgdir)
    sys.path.insert(0, pkgdir)

    if args.query:
        verbose = args.query
    else:
        verbose = args.verbose

    makestubs(builddir, args.xmldir, args.srcdir,
              force_update=args.force_update,
              verbose=verbose, processes=args.processes)
    if args.release:
        outputdir = os.path.join(builddir, 'pyi')
        print("Releasing to %s" % pkgdir)
        copy_tree(outputdir, pkgdir)
        touch(os.path.join(pkgdir, 'pxr', 'py.typed'))


if __name__ == '__main__':
    main()
