import cgi
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Union
from urllib.parse import urlparse

import click
import requests
from click.exceptions import ClickException
from click_aliases import ClickAliasedGroup

MIRROR_DIR = Path.home().joinpath(".mirror")
DB_PATH = MIRROR_DIR.joinpath("db")
SAVE_DIR = MIRROR_DIR.joinpath("bin")

conn: sqlite3.Connection
cursor: sqlite3.Cursor

class OctalParamType(click.ParamType):
	"""chmod-like octal parameters"""
	name = "integer"
	
	def convert(self, value, param, ctx):
		try:
			return int(value, 8)
		except ValueError:
			self.fail(f"{value!r} is not a valid octal integer", param, ctx)

OCTAL_PARAM = OctalParamType()

def main():
	global conn
	global cursor
	#create dirs
	SAVE_DIR.mkdir(parents=True, exist_ok=True)
	DB_PATH.touch(exist_ok=True)
	#setup db
	conn = sqlite3.connect(DB_PATH)
	cursor = conn.cursor()
	with conn:
		cursor.execute(
			"""CREATE TABLE IF NOT EXISTS mirrors (
				filename text, 
				url text, 
				archive_filename text, 
				post_install text
			);"""
		)
	#run cli
	mirror()
	conn.close()

@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=ClickAliasedGroup)
def mirror():
	pass

@mirror.command(aliases=["addf", "add", "a"])
@click.argument("url")
@click.option("--filename", "-f", type=Path)
@click.option("--mode", "-m", type=OCTAL_PARAM, default="755", show_default=True)
@click.option(
	"--post-install", "--post", "-p", help="arbitary shell script to run after installation"
)
def add_file(url: str, filename: Path, mode: int, post_install: str):
	print(f"Adding {url}")
	try:
		filename = download_file(url, filename)
	except (ValueError, FileExistsError, requests.exceptions.HTTPError) as e:
		raise ClickException(str(e)) from e
	filename.chmod(mode)
	
	run_post_install(filename, post_install)
	
	with conn:
		cursor.execute(
			"INSERT INTO mirrors VALUES(?, ?, ?, ?)", (str(filename), url, None, post_install)
		)
	print(f"Added {url} at {shorten_path(filename)}")

@mirror.command(aliases=["add-ar", "adda"])
@click.argument("url")
@click.argument("archive_filename")
@click.option("--filename", "-f", type=Path)
@click.option("--mode", "-m", type=OCTAL_PARAM, default="755", show_default=True)
@click.option(
	"--post-install", "--post", "-p", help="arbitary shell script to run after installation"
)
def add_archive(url: str, archive_filename: str, filename: Path, mode: int, post_install: str):
	print(f"Adding archive {url}")
	try:
		filename = download_file(url, filename, archive_filename)
	except (ValueError, FileExistsError) as e:
		raise ClickException(str(e)) from e
	filename.chmod(mode)
	
	run_post_install(filename, post_install)
	
	with conn:
		cursor.execute(
			"INSERT INTO mirrors VALUES(?, ?, ?, ?)",
			(str(filename), url, archive_filename, post_install)
		)
	print(f"Added archive {url} at {shorten_path(filename)}")

@mirror.command(aliases=["list", "ls", "l"])
def list_files():
	with conn:
		print("Mirrors:")
		mirrors = cursor.execute("SELECT filename, url FROM mirrors")
		found = False
		for filename, url in mirrors:
			found = True
			print(f"{shorten_path(filename):30} {url}")
		if not found:
			print("None")

@mirror.command(aliases=["update", "u"])
def update_files():
	with conn:
		for filename, url, archive_filename, post_install in cursor.execute(
			"SELECT filename, url, archive_filename, post_install FROM mirrors"
		):
			filename = Path(filename)
			print(f"Updating {shorten_path(filename)} with {url}")
			try:
				download_file(url, filename, archive_filename, exist_ok=True)
			except (ValueError, FileExistsError, requests.exceptions.HTTPError) as e:
				raise ClickException(str(e)) from e
			
			run_post_install(filename, post_install)
	print("Updated!")

@mirror.command(aliases=["rm", "r"])
@click.argument("filename")
@click.option("--glob", "-g", is_flag=True)
def remove_file(filename: str, glob: bool):
	path = Path(filename).expanduser().resolve()  #convert relative path to absolute
	print(f"Deleting {shorten_path(path)}")
	#error handling
	if not path.exists():
		print("Warning: File doesn't exist in filesystem")
	if not glob and not file_in_db(path):
		raise ClickException(f"File {shorten_path(path)} not in database")
	#handle db
	with conn:
		if glob:
			conn.execute("DELETE FROM mirrors WHERE filename GLOB ?", (str(path), ))
		else:
			conn.execute("DELETE FROM mirrors WHERE filename = ?", (str(path), ))
	print(f"Deleted {shorten_path(path)}")

@mirror.command()
def delete_db():
	if click.confirm('Are you sure you want to delete the database?'):
		shutil.rmtree(SAVE_DIR)
		DB_PATH.unlink()

@mirror.command(aliases=["sqlite"])
def sqlite_shell():
	subprocess.run(["sqlite3", DB_PATH], check=False)

def download_file(
	url: str,
	filename: Union[str, Path],
	archive_filename: str = None,
	exist_ok: bool = False
) -> Path:
	resp = requests.get(url)
	resp.raise_for_status()
	#get the filename from the response
	try:
		resp_filename = SAVE_DIR.joinpath("a").with_name(
			cgi.parse_header(resp.headers["Content-Disposition"])[1]["filename"]
		)
	except KeyError:
		resp_filename = SAVE_DIR.joinpath(urlparse(url).path.split("/")[-1])
	
	if filename is None:
		if archive_filename is not None:
			filename = SAVE_DIR.joinpath(archive_filename)
		else:
			filename = resp_filename
	else:
		filename = Path(filename)
	
	filename = filename.expanduser().resolve()  # convert relative paths to absolute paths
	if filename == SAVE_DIR:
		raise ValueError("Empty filename")
	#check if file exists in db
	if filename.exists() and not exist_ok:
		if file_in_db(filename):
			raise ValueError(f"File {shorten_path(filename)} already in database")
		else:
			print(f"Warning: File {shorten_path(filename)} already exists")
	if archive_filename is None:
		with open(filename, "wb") as f:
			f.write(resp.content)
	else:
		# archive handling
		# uses partition on .name instead of .suffix due to .tar.gz
		with NamedTemporaryFile(suffix="." + resp_filename.name.partition(".")[2]) as f:
			with TemporaryDirectory() as tmp_dir:
				#unpack into tmp_dir
				f.write(resp.content)
				shutil.unpack_archive(f.name, tmp_dir)
				#path to copy from
				old_path = Path(tmp_dir).joinpath(archive_filename)
				if old_path.is_file():
					shutil.copyfile(old_path, filename)
				elif old_path.is_dir():
					try:
						shutil.rmtree(filename)
					except FileNotFoundError:  #ignore if target directory doesn't exist
						pass
					shutil.copytree(old_path, filename)
				else:
					raise ValueError(f"File {archive_filename} isn't a file or a directory")
	return filename

def run_post_install(filename: Path, post_install: str):
	if post_install is not None:
		os.chdir(filename.parent)
		try:
			subprocess.run(post_install, shell=True, check=True)
		except subprocess.CalledProcessError as e:
			raise ClickException(str(e)) from e

def file_in_db(filename: Path) -> bool:
	"""check if file is in the database"""
	with conn:
		cursor.execute("SELECT COUNT(filename) FROM mirrors WHERE filename = ?", (str(filename), ))
		return cursor.fetchone()[0] > 0

def shorten_path(filename: Union[str, Path]) -> str:
	"""shorten /home/user/ to ~"""
	filename = Path(filename)
	try:
		return "~/" + str(filename.relative_to(Path.home()))
	except ValueError:
		return str(filename)

if __name__ == "__main__":
	main()
