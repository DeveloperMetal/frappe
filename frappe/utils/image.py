# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals, print_function

import frappe

from frappe.utils import cint
import io
import os

def resize_images(path, maxdim=700):
	import Image
	size = (maxdim, maxdim)
	for basepath, folders, files in os.walk(path):
		for fname in files:
			extn = fname.rsplit(".", 1)[1]
			if extn in ("jpg", "jpeg", "png", "gif"):
				im = Image.open(os.path.join(basepath, fname))
				if im.size[0] > size[0] or im.size[1] > size[1]:
					im.thumbnail(size, Image.ANTIALIAS)
					im.save(os.path.join(basepath, fname))

					print("resized {0}".format(os.path.join(basepath, fname)))

def process_thumbnail(path, commands):

	if ('.' not in path):
		return False

	extn = path.rsplit('.', 1)[-1]
	print("[%s] EXTENSION" % extn)

	if extn not in ('jpg', 'jpeg', 'png', 'gif', 'bmp'):
		return False

	from PIL import Image

	filepath = frappe.utils.get_site_path(path)
	print("[%s] Site path" % filepath)
	try:
		img = Image.open(filepath)
	except IOError:
		raise NotFound

	# capture desired image width and height
	width = cint(commands.width or 0) or cint(commands.size or 0)
	height = cint(commands.height or 0) or cint(commands.size or 0)

	# default setting width to height and viceversa when either one is missing
	# also defaults to 300x300 pixels max size when size information is missing
	width = width or height or 300
	height = height or width or 300
	size = (width, height)

	# Defaults sampling to antialiasing
	resample = [
		Image.NEAREST,
		Image.BOX,
		Image.BILINEAR,
		Image.HAMMING,
		Image.BICUBIC,
		Image.LANCZOS,
	][cint(commands.resample or 2)]

	# Defaults quality for JPG only
	quality = cint(commands.quality or 75)

	# Actual image resize
	img.thumbnail(size, resample)

	# Extract bytes
	buffer = io.BytesIO()

	# Enforce image format
	format = {
		"JPEG": "JPEG",
		"JPG": "JPEG",
		"PNG": "PNG",
		"BMP": "BMP",
		"GIF": "GIF"
	}.get(extn.upper(), "JPEG")

	# default image options for PIL processing
	image_options = dict(
		optimize=True, 
		progressive=True, 
		quality=quality
	)

	if format == "GIF":
		# For GIF Animations only
		image_options["save_all"] = True
	
	img.save(buffer, 
		format=format, 
		**image_options
	)

	# Return image bytes
	return buffer

