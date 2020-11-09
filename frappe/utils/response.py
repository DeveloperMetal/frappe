# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import json
import datetime
import decimal
import mimetypes
import os
import frappe
from frappe import _
import frappe.model.document
import frappe.utils
import frappe.sessions
import werkzeug.utils
from werkzeug.local import LocalProxy
from werkzeug.wsgi import wrap_file
from werkzeug.wrappers import Response
from werkzeug.exceptions import NotFound, Forbidden
from frappe.website.render import render
from frappe.utils import cint
from six import text_type
from six.moves.urllib.parse import quote
from frappe.core.doctype.access_log.access_log import make_access_log


def report_error(status_code):
	'''Build error. Show traceback in developer mode'''
	if (cint(frappe.db.get_system_setting('allow_error_traceback'))
		and (status_code!=404 or frappe.conf.logging)
		and not frappe.local.flags.disable_traceback):
		frappe.errprint(frappe.utils.get_traceback())

	response = build_response("json")
	response.status_code = status_code
	return response

def build_response(response_type=None):
	if "docs" in frappe.local.response and not frappe.local.response.docs:
		del frappe.local.response["docs"]

	response_type_map = {
		'csv': as_csv,
		'txt': as_txt,
		'download': as_raw,
		'json': as_json,
		'pdf': as_pdf,
		'page': as_page,
		'redirect': redirect,
		'binary': as_binary
	}

	return response_type_map[frappe.response.get('type') or response_type]()

def as_csv():
	response = Response()
	response.mimetype = 'text/csv'
	response.charset = 'utf-8'
	response.headers["Content-Disposition"] = ("attachment; filename=\"%s.csv\"" % frappe.response['doctype'].replace(' ', '_')).encode("utf-8")
	response.data = frappe.response['result']
	return response

def as_txt():
	response = Response()
	response.mimetype = 'text'
	response.charset = 'utf-8'
	response.headers["Content-Disposition"] = ("attachment; filename=\"%s.txt\"" % frappe.response['doctype'].replace(' ', '_')).encode("utf-8")
	response.data = frappe.response['result']
	return response

def as_raw():
	response = Response()
	response.mimetype = frappe.response.get("content_type") or mimetypes.guess_type(frappe.response['filename'])[0] or "application/unknown"
	response.headers["Content-Disposition"] = ("attachment; filename=\"%s\"" % frappe.response['filename'].replace(' ', '_')).encode("utf-8")
	response.data = frappe.response['filecontent']
	return response

def as_json():
	make_logs()
	response = Response()
	if frappe.local.response.http_status_code:
		response.status_code = frappe.local.response['http_status_code']
		del frappe.local.response['http_status_code']

	response.mimetype = 'application/json'
	response.charset = 'utf-8'
	response.data = json.dumps(frappe.local.response, default=json_handler, separators=(',',':'))
	return response

def as_pdf():
	response = Response()
	response.mimetype = "application/pdf"
	encoded_filename = quote(frappe.response['filename'].replace(' ', '_'))
	response.headers["Content-Disposition"] = ("filename=\"%s\"" % frappe.response['filename'].replace(' ', '_') + ";filename*=utf-8''%s" % encoded_filename).encode("utf-8")
	response.data = frappe.response['filecontent']
	return response

def as_binary():
	response = Response()
	response.mimetype = 'application/octet-stream'
	response.headers["Content-Disposition"] = ("filename=\"%s\"" % frappe.response['filename'].replace(' ', '_')).encode("utf-8")
	response.data = frappe.response['filecontent']
	return response

def make_logs(response = None):
	"""make strings for msgprint and errprint"""
	if not response:
		response = frappe.local.response

	if frappe.error_log:
		response['exc'] = json.dumps([frappe.utils.cstr(d["exc"]) for d in frappe.local.error_log])

	if frappe.local.message_log:
		response['_server_messages'] = json.dumps([frappe.utils.cstr(d) for
			d in frappe.local.message_log])

	if frappe.debug_log and frappe.conf.get("logging") or False:
		response['_debug_messages'] = json.dumps(frappe.local.debug_log)

	if frappe.flags.error_message:
		response['_error_message'] = frappe.flags.error_message

def json_handler(obj):
	"""serialize non-serializable data for json"""
	# serialize date
	import collections

	if isinstance(obj, (datetime.date, datetime.timedelta, datetime.datetime)):
		return text_type(obj)

	elif isinstance(obj, decimal.Decimal):
		return float(obj)

	elif isinstance(obj, LocalProxy):
		return text_type(obj)

	elif isinstance(obj, frappe.model.document.BaseDocument):
		doc = obj.as_dict(no_nulls=True)
		return doc

	elif isinstance(obj, collections.Iterable):
		return list(obj)

	elif type(obj)==type or isinstance(obj, Exception):
		return repr(obj)

	else:
		raise TypeError("""Object of type %s with value of %s is not JSON serializable""" % \
						(type(obj), repr(obj)))

def as_page():
	"""print web page"""
	return render(frappe.response['route'], http_status_code=frappe.response.get("http_status_code"))

def redirect():
	return werkzeug.utils.redirect(frappe.response.location)

def download_backup(path):
	try:
		frappe.only_for(("System Manager", "Administrator"))
		make_access_log(report_name='Backup')
	except frappe.PermissionError:
		raise Forbidden(_("You need to be logged in and have System Manager Role to be able to access backups."))

	return send_private_file(path)

def download_private_file(path):
	"""Checks permissions and sends back private file"""

	files = frappe.db.get_all('File', {'file_url': path})
	can_access = False
	# this file might be attached to multiple documents
	# if the file is accessible from any one of those documents
	# then it should be downloadable
	for f in files:
		_file = frappe.get_doc("File", f)
		can_access = _file.is_downloadable()
		if can_access:
			make_access_log(doctype='File', document=_file.name, file_type=os.path.splitext(path)[-1][1:])
			break

	if not can_access:
		raise Forbidden(_("You don't have permission to access this file"))

	return send_private_file(path.split("/private", 1)[1])

def process_thumbnail(path):
	"""Processes a /thumbnail/<image file> path with optional resize, resampling and
	quality settings while keeping its aspect ratio intact.

	Examples: 
	
	/thumbnail/myimage.jpg?size=300&resample=0&quality=20
	/thumbnail/myimage.jpg?width=300&resample=0&quality=20
	/thumbnail/myimage.jpg?height=300&resample=0&quality=20
	/thumbnail/myimage.jpg?width=200&height=300&resample=0&quality=20

	Options:

		- width: The maximum image width desired in pixels.

		- height: The maximum image height desired in pixels.

		- size: A single value for maximum image width and height desired in pixels.

		- resample: Sampling filter value from 0 to 5. See below from worst(fastest) to best(slowest).
			Defaults to 2 - BILINEAR:

			- 0: Nearest - Pick one nearest pixel from the input image. Ignore all other input pixels.
			- 1: Box - Each pixel of source image contributes to one pixel of the destination image with identical weights
			- 2: Bilinear - For resize calculate the output pixel value using linear interpolation on all pixels that may contribute to the output value
			- 3: Hamming - Produces a sharper image than BILINEAR, doesn’t have dislocations on local level like with BOX.
			- 4: Bicubic - For resize calculate the output pixel value using cubic interpolation on all pixels that may contribute to the output value
			- 5: Lanczos - Calculate the output pixel value using a high-quality Lanczos filter (a truncated sinc) on all pixels that may contribute to the output value

		- quality: For JPG images from 0(smallest files size) worst to 100(largest file size) best. Defaults to 75
	"""

	# Transform thumbnail path to public/files/ path
	file_path = os.path.join('public', 'files', *os.path.split(path)[1:])
	filename = os.path.basename(file_path)

	# Build options query for caching id
	options = '&'.join([ 
		"%s=%s" % (key, value) \
		for key, value in frappe.local.form_dict.items() \
			if key in ["size", "width", "height", "resample", "quality"]
	])

	# Build cache path for this image and retrieve data
	cache_path = "{}?{}".format(path, options)
	buffer = frappe.cache().hget("thumbnail_cache", cache_path)

	if not buffer:
		from frappe.utils.image import process_thumbnail
		buffer = process_thumbnail(file_path, frappe.local.form_dict)

		# set cache only when generating a new thumbnail
		frappe.cache().hset("thumbnail_cache", cache_path, buffer)

	if buffer:
		response = Response(buffer.getvalue(), direct_passthrough=True)
		response.mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
		return response

	else:
		from werkzeug.exceptions import HTTPException, NotFound
		raise NotFound

def send_private_file(path):
	path = os.path.join(frappe.local.conf.get('private_path', 'private'), path.strip("/"))
	filename = os.path.basename(path)

	if frappe.local.request.headers.get('X-Use-X-Accel-Redirect'):
		path = '/protected/' + path
		response = Response()
		response.headers['X-Accel-Redirect'] = quote(frappe.utils.encode(path))

	else:
		filepath = frappe.utils.get_site_path(path)
		try:
			f = open(filepath, 'rb')
		except IOError:
			raise NotFound

		response = Response(wrap_file(frappe.local.request.environ, f), direct_passthrough=True)

	# no need for content disposition and force download. let browser handle its opening.
	# Except for those that can be injected with scripts.

	extension = os.path.splitext(path)[1]
	blacklist = ['.svg', '.html', '.htm', '.xml']

	if extension.lower() in blacklist:
		response.headers.add('Content-Disposition', 'attachment', filename=filename.encode("utf-8"))

	response.mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

	return response

def handle_session_stopped():
	frappe.respond_as_web_page(_("Updating"),
		_("Your system is being updated. Please refresh again after a few moments"),
		http_status_code=503, indicator_color='orange', fullpage = True, primary_action=None)
	return frappe.website.render.render("message", http_status_code=503)
