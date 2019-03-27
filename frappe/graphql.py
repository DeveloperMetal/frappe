import frappe
import graphene

DTTYPES = {}
ROOTQL = None

def normalize_doctype_name(name):
  return str(name.title().replace(" ", "").replace("-", "_"))

class DoctypeSchemaInfo:
  def __init__(self, name):
    self.meta = frappe.get_meta(name)
    self.normalized_name = normalize_doctype_name(name)
    self.name = name
    self.fields = {}

    self.build_fields()

  def build_link_field(self, field, common):
    return graphene.Field(lambda: DTTYPES[field.options].docType, resolver=self.linkResolver, **common)

  def build_fields(self):
    self.fields = {
      'name': graphene.ID(required=True, description="The doctype identity field.")
    }

    for field in self.meta.fields:
      if field.fieldname[0].isdigit():
        continue

      fieldCommon = dict(
        description=field.description,
      )

      fieldtype = None
      if field.fieldtype == 'Data':
        fieldtype = graphene.String(**fieldCommon)
      elif field.fieldtype == 'Int':
        fieldtype = graphene.Int(**fieldCommon)
      elif field.fieldtype == 'Float':
        fieldtype = graphene.Float(**fieldCommon)
      elif field.fieldtype == 'Check':
        fieldtype = graphene.Boolean(**fieldCommon)
      elif field.fieldtype == 'Datetime':
        fieldtype = graphene.types.datetime.DateTime(**fieldCommon)
      elif field.fieldtype == 'Date':
        fieldtype = graphene.types.datetime.Date(**fieldCommon)
      elif field.fieldtype == 'Time':
        fieldtype = graphene.types.datetime.Time(**fieldCommon)
      elif field.fieldtype == 'Link':
        fieldtype = self.build_link_field(field, fieldCommon)
      #elif field.fieldtype in ('Dynamic Link'):
        # we'll add these link fields after all doctypes have a schema class
        # so we have a type to link to at the ready.
        #self.defered_fields.append(field)

      if fieldtype:
        self.fields[field.fieldname] = fieldtype

  def build_list_type(self):

    fields = {
      'start': graphene.Int(),
      'limit': graphene.Int(),
      'total': graphene.Int(),
      'count': graphene.Int(),
      'result': graphene.List(self.docType, required=True)
    }

    self.listType = type('List{}Results'.format(self.normalized_name), (graphene.ObjectType,), fields)

  def build_type(self):
    self.docType = type(self.normalized_name, (graphene.ObjectType,), self.fields)
    self.build_list_type()
    self.build_filters_type()

  def build_filters_type(self):
    self.filtersType = type('List{}Filters'.format(self.normalized_name), (graphene.ObjectType,), self.fields)

  def listResolver(self, root, info, limit=20, start=0, filters=None):
    available_fields = self.fields.keys()
    # build a list of fields we are required to fetch and make sure they exist

    fields = []
    find_total = False
    listResultFields = dict(start=start, limit=limit)
    result_field = None
    for field in info.field_asts[0].selection_set.selections:
      if field.name.value == 'result':
        result_field = field
      elif field.name.value == 'total':
        find_total = True
        fields.append('count(`tab{}`.name) as __total_rows'.format(self.name))

    # make sure we only fetch required fields
    fields += [selection.name.value \
      for selection in result_field.selection_set.selections \
        if selection.name.value in available_fields ]

    listResultFields['result'] = frappe.get_list(self.name, fields=fields, limit_page_length=limit, limit_start=start)
    listResultFields['count'] = len(listResultFields['result'])

    if find_total and listResultFields['count'] > 0:
      listResultFields['total'] = listResultFields['result'][0]['__total_rows']
   
    return self.listType(**listResultFields)

  def docResolver(self, root, info, name):
    available_fields = self.fields.keys()
    # build a list of fields we are required to fetch and make sure they exist
    fields = [selection.name.value \
      for selection in info.field_asts[0].selection_set.selections \
        if selection.name.value in available_fields ]

    doc = frappe.get_list(self.name, fields=fields, filters={"name": name})[0]
    return doc

  def linkResolver(self, root, info):
    field = self.meta.get_field(info.field_name)
    dt = field.options
    name = root[info.field_name]
    return DTTYPES[dt].docResolver(root, info, name=name)

def handle():

  query_parm = frappe.local.form_dict['query']
  schema = ROOTQL if ROOTQL != None else build_schema()
  result = schema.execute(query_parm)

  frappe.local.response.update(result.to_dict())

def build_schema():
  
  schema_types = {}
  for dt in frappe.get_list("DocType"):
    # skip doctypes that start with a digit for now until we
    # figure out a consensus to rename these
    if dt.name[0].isdigit():
      continue

    dtInfo = DTTYPES[dt.name] = DTTYPES[dt.name] if dt.name in DTTYPES else DoctypeSchemaInfo(dt.name)

    dtInfo.build_type()

    # list query schema
    # All<Doctype>s(limit: 20, start: 0) {
    #   total
    #   count
    #   start
    #   limit
    #   result {
    #     <fields> 
    #   }
    # }
    schema_types["All{}s".format(dtInfo.normalized_name)] = graphene.Field(
      dtInfo.listType, 
      limit=graphene.Int(default_value=20),
      start=graphene.Int(default_value=0),
      resolver=dtInfo.listResolver)

    # single doctype query schema
    # <Doctype>(name: <name>) { <fields> }
    schema_types[dtInfo.normalized_name] = graphene.Field(dtInfo.docType, name=graphene.ID(), resolver=dtInfo.docResolver)

  # root schema
  Query = type('Query', (graphene.ObjectType,), schema_types)

  ROOTQL = graphene.Schema(query=Query, auto_camelcase=False)
  return ROOTQL