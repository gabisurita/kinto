from __future__ import division
import pydoc
import warnings
import re

import colander

from kinto.core.schema import (Any, HeaderField, QueryField, HeaderQuotedInteger,
                               FieldList, TimeStamp, URL)
from kinto.core.utils import native_value
from kinto.core.storage import StorageBase

POSTGRESQL_MAX_INTEGER_VALUE = 2**64 // 2

positive_big_integer = colander.Range(min=0, max=POSTGRESQL_MAX_INTEGER_VALUE)


class TimeStamp(TimeStamp):
    """This schema is deprecated, you shoud use `kinto.core.schema.TimeStamp` instead."""

    def __init__(self, *args, **kwargs):
        message = ("`kinto.core.resource.schema.TimeStamp` is deprecated, "
                   "use `kinto.core.schema.TimeStamp` instead.")
        warnings.warn(message, DeprecationWarning)
        super(TimeStamp, self).__init__(*args, **kwargs)


class URL(URL):
    """This schema is deprecated, you shoud use `kinto.core.schema.URL` instead."""

    def __init__(self, *args, **kwargs):
        message = ("`kinto.core.resource.schema.URL` is deprecated, "
                   "use `kinto.core.schema.URL` instead.")
        warnings.warn(message, DeprecationWarning)
        super(URL, self).__init__(*args, **kwargs)


# Resource related schemas


class ResourceSchema(colander.MappingSchema):
    """Base resource schema, with *Cliquet* specific built-in options."""

    class Options:
        """
        Resource schema options.

        This is meant to be overriden for changing values:

        .. code-block:: python

            class Product(ResourceSchema):
                reference = colander.SchemaNode(colander.String())

                class Options:
                    readonly_fields = ('reference',)
        """
        readonly_fields = tuple()
        """Fields that cannot be updated. Values for fields will have to be
        provided either during record creation, through default values using
        ``missing`` attribute or implementing a custom logic in
        :meth:`kinto.core.resource.UserResource.process_record`.
        """

        preserve_unknown = True
        """Define if unknown fields should be preserved or not.

        The resource is schema-less by default. In other words, any field name
        will be accepted on records. Set this to ``False`` in order to limit
        the accepted fields to the ones defined in the schema.
        """

    @classmethod
    def get_option(cls, attr):
        default_value = getattr(ResourceSchema.Options, attr)
        return getattr(cls.Options, attr,  default_value)

    @classmethod
    def is_readonly(cls, field):
        """Return True if specified field name is read-only.

        :param str field: the field name in the schema
        :returns: ``True`` if the specified field is read-only,
            ``False`` otherwise.
        :rtype: bool
        """
        return field in cls.get_option("readonly_fields")

    def schema_type(self):
        if self.get_option("preserve_unknown") is True:
            unknown = 'preserve'
        else:
            unknown = 'ignore'
        return colander.Mapping(unknown=unknown)


class PermissionsSchema(colander.SchemaNode):
    """A permission mapping defines ACEs.

    It has permission names as keys and principals as values.

    ::

        {
            "write": ["fxa:af3e077eb9f5444a949ad65aa86e82ff"],
            "groups:create": ["fxa:70a9335eecfe440fa445ba752a750f3d"]
        }

    """

    def __init__(self, *args, **kwargs):
        self.known_perms = kwargs.pop('permissions', tuple())
        super(PermissionsSchema, self).__init__(*args, **kwargs)

        for perm in self.known_perms:
            self[perm] = self._get_node_principals(perm)

    def schema_type(self):
        if self.known_perms:
            return colander.Mapping(unknown='raise')
        else:
            return colander.Mapping(unknown='preserve')

    def deserialize(self, cstruct=colander.null):

        # If permissions are not a mapping (e.g null or invalid), try deserializing
        if not isinstance(cstruct, dict):
            return super(PermissionsSchema, self).deserialize(cstruct)

        # If permissions are listed, check fields and produce fancy error messages
        if self.known_perms:
            for perm in cstruct:
                colander.OneOf(choices=self.known_perms)(self, perm)
            return super(PermissionsSchema, self).deserialize(cstruct)

        # Else deserialize the fields that are not on the schema
        permissions = {}
        perm_schema = colander.SequenceSchema(colander.SchemaNode(colander.String()))
        for perm, principals in cstruct.items():
            permissions[perm] = perm_schema.deserialize(principals)

        return permissions

    def _get_node_principals(self, perm):
        principal = colander.SchemaNode(colander.String())
        return colander.SchemaNode(colander.Sequence(), principal, name=perm,
                                   missing=colander.drop)


# Header schemas


class HeaderSchema(colander.MappingSchema):
    """Base schema used for validating and deserializing request headers. """

    missing = colander.drop

    if_match = HeaderQuotedInteger(name='If-Match')
    if_none_match = HeaderQuotedInteger(name='If-None-Match')

    @staticmethod
    def schema_type():
        return colander.Mapping(unknown='preserve')


class PatchHeaderSchema(HeaderSchema):
    """Header schema used with PATCH requests."""

    def response_behavior_validator():
        return colander.OneOf(['full', 'light', 'diff'])

    response_behaviour = HeaderField(colander.String(), name='Response-Behavior',
                                     validator=response_behavior_validator())


# Querystring schemas


class QuerySchema(colander.MappingSchema):
    """
    Schema used for validating and deserializing querystrings. It will include
    and try to guess the type of unknown fields (field filters) on deserialization.
    """
    missing = colander.drop

    @staticmethod
    def schema_type():
        return colander.Mapping(unknown='ignore')

    def deserialize(self, cstruct=colander.null):
        """
        Deserialize and validate the QuerySchema fields and try to deserialize and
        get the native value of additional filds (field filters) that may be present
        on the cstruct.

        e.g:: ?exclude_id=a,b&deleted=true -> {'exclude_id': ['a', 'b'], deleted: True}
        """
        values = {}

        schema_values = super(QuerySchema, self).deserialize(cstruct)
        if schema_values is colander.drop:
            return schema_values

        # Deserialize querystring field filters (see docstring e.g)
        for k, v in cstruct.items():
            # Deserialize lists used on in_ and exclude_ filters
            if k.startswith('in_') or k.startswith('exclude_'):
                as_list = FieldList().deserialize(v)
                if isinstance(as_list, list):
                    values[k] = [native_value(v) for v in as_list]
            else:
                values[k] = native_value(v)

        values.update(schema_values)
        return values


class CollectionQuerySchema(QuerySchema):
    """Querystring schema used with collections."""

    _limit = QueryField(colander.Integer(), validator=positive_big_integer)
    _sort = FieldList()
    _token = QueryField(colander.String())
    _since = QueryField(colander.Integer(), validator=positive_big_integer)
    _to = QueryField(colander.Integer(), validator=positive_big_integer)
    _before = QueryField(colander.Integer(), validator=positive_big_integer)
    id = QueryField(colander.String())
    last_modified = QueryField(colander.Integer(), validator=positive_big_integer)


class RecordGetQuerySchema(QuerySchema):
    """Querystring schema for GET record requests."""

    _fields = FieldList()


class CollectionGetQuerySchema(CollectionQuerySchema):
    """Querystring schema for GET collection requests."""

    _fields = FieldList()


# Body Schemas


class RecordSchema(colander.MappingSchema):

    @colander.deferred
    def data(node, kwargs):
        data = kwargs.get('data')
        if data:
            # Check if empty record is allowed.
            # (e.g every schema fields have defaults)
            try:
                data.deserialize({})
            except colander.Invalid:
                pass
            else:
                data.default = {}
                data.missing = colander.drop
        return data

    @colander.deferred
    def permissions(node, kwargs):
        def get_perms(node, kwargs):
            return kwargs.get('permissions')
        # Set if node is provided, else keep deferred. This allows binding the body
        # on Resource first and bind permissions later if using SharableResource.
        return get_perms(node, kwargs) or colander.deferred(get_perms)

    @staticmethod
    def schema_type():
        return colander.Mapping(unknown='raise')


class JsonPatchOperationSchema(colander.MappingSchema):
    """Single JSON Patch Operation."""

    def op_validator():
        op_values = ['test', 'add', 'remove', 'replace', 'move', 'copy']
        return colander.OneOf(op_values)

    def path_validator():
        return colander.Regex('(/\w*)+')

    op = colander.SchemaNode(colander.String(), validator=op_validator())
    path = colander.SchemaNode(colander.String(), validator=path_validator())
    from_ = colander.SchemaNode(colander.String(), name='from',
                                validator=path_validator(), missing=colander.drop)
    value = colander.SchemaNode(Any(), missing=colander.drop)

    @staticmethod
    def schema_type():
        return colander.Mapping(unknown='raise')


class JsonPatchBodySchema(colander.SequenceSchema):
    """Body used with JSON Patch (application/json-patch+json) as in RFC 6902."""

    operations = JsonPatchOperationSchema(missing=colander.drop)


# Request schemas


class RequestSchema(colander.MappingSchema):
    """Base schema for kinto requests."""

    @colander.deferred
    def path(node, kwargs):
        def build_path(node, kwargs):
            """Build a path request schema from a cornice path and resource_id
            generators."""
            path = kwargs.get('path')
            # If not defined, keep deferred
            if not path:
                return

            # Try to replace {id} with the current resource id
            current_resource_id = kwargs.get('resource_name')
            if current_resource_id:
                path = path.replace('{id}', '{{{}_id}}'.format(current_resource_id))

            # Match all ids and remove brackets
            resource_ids = [name[1:-1] for name in re.findall('\{.*?\}', path)]

            # Get id generators
            id_generators = kwargs.get('id_generators', [])

            path_schema = colander.MappingSchema(name='path')

            # build a path request schema node for each resource
            for rid in resource_ids:
                # try to get a config setted id_generator for the resource
                try:
                    resource_id_gen = id_generators.get('{}_generator'.format(rid))
                    default_id_gen = id_generators.get('id_generator')
                    id_gen = resource_id_gen or default_id_gen
                    id_gen = pydoc.locate(id_gen)
                    regexp = id_gen.regexp

                # Get the basic storage generator
                except AttributeError:
                    regexp = StorageBase.id_generator.regexp

                # Reset the current resource id
                if rid == current_resource_id + '_id':
                    rid = 'id'

                # Build the corresponding validator and SchemaNode
                validator = colander.Regex(regexp, msg="")
                path_schema[rid] = colander.SchemaNode(colander.String(),
                                                       validator=validator)

            return path_schema

        # Set if node is provided, else keep deferred (allow bindind later)
        return build_path(node, kwargs) or colander.deferred(build_path)

    @colander.deferred
    def header(node, kwargs):
        return kwargs.get('header')

    @colander.deferred
    def querystring(node, kwargs):
        return kwargs.get('querystring')

    def after_bind(self, node, kw):
        # Set default bindings
        if not self.get('header'):
            self['header'] = HeaderSchema()
        if not self.get('querystring'):
            self['querystring'] = QuerySchema()

    def deserialize(self, cstruct=colander.null):
        print(cstruct)
        deserialized = super().deserialize(cstruct)
        print(deserialized)
        return deserialized


class PayloadRequestSchema(RequestSchema):
    """Base schema for methods that use a JSON request body."""

    @colander.deferred
    def body(node, kwargs):
        def get_body(node, kwargs):
            return kwargs.get('body')
        # Set if node is provided, else keep deferred (and allow bindind later)
        return get_body(node, kwargs) or colander.deferred(get_body)


class JsonPatchRequestSchema(RequestSchema):
    """JSON Patch (application/json-patch+json) request schema."""

    body = JsonPatchBodySchema()
    querystring = QuerySchema()
    header = PatchHeaderSchema()
