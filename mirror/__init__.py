import cgi
import shutil
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile
from typing import Union
from urllib.parse import urlparse

import click
from click.exceptions import ClickException
import requests
from click_aliases import ClickAliasedGroup

MIRROR_DIR = Path.home().joinpath(".mirror")
DB_PATH = MIRROR_DIR.joinpath("db")
SAVE_DIR = MIRROR_DIR.joinpath("bin")
conn: sqlite3.Connection = None
cursor: sqlite3.Cursor = None

class OctalParamType(click.ParamType):
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
	SAVE_DIR.mkdir(parents=True, exist_ok=True)
	DB_PATH.touch(exist_ok=True)
	conn = sqlite3.connect(DB_PATH)
	cursor = conn.cursor()
	with conn:
		cursor.execute(
			"CREATE TABLE IF NOT EXISTS mirrors (filename text, url text, archive_filename text);"
		)
	mirror()
	conn.close()

@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=ClickAliasedGroup)
def mirror():
	pass

@mirror.command(aliases=["addf", "add", "a"])
@click.argument("url")
@click.option("--filename", "-f", type=Path)
@click.option("--mode", "-m", type=OCTAL_PARAM, default="755", show_default=True)
def add_file(url: str, filename: Path, mode: int):
	print(f"Adding {url}")
	try:
		filename = download_file(url, filename)
	except (ValueError, FileExistsError) as e:
		raise ClickException(str(e)) from e
	filename.chmod(mode)
	with conn:
		cursor.execute("INSERT INTO mirrors VALUES(?, ?, ?)", (str(filename), url, None))
	print(f"Added {url} at {shortern_path(filename)}")

@mirror.command(aliases=["add-ar", "adda"])
@click.argument("url")
@click.argument("archive_filename")
@click.option("--filename", "-f", type=Path)
@click.option("--mode", "-m", type=OCTAL_PARAM, default="755", show_default=True)
def add_archive(url: str, archive_filename, filename: Path, mode: int):
	print(f"Adding archive {url}")
	try:
		filename = download_file(url, filename, archive_filename)
	except (ValueError, FileExistsError) as e:
		raise ClickException(str(e)) from e
	filename.chmod(mode)
	with conn:
		cursor.execute(
			"INSERT INTO mirrors VALUES(?, ?, ?)", (str(filename), url, archive_filename)
		)
	print(f"Added archive {url} at {shortern_path(filename)}")

@mirror.command(aliases=["list", "ls", "l"])
def list_files():
	with conn:
		print("Mirrors:")
		mirrors = cursor.execute("SELECT filename, url FROM mirrors")
		found = False
		for filename, url in mirrors:
			found = True
			print(f"{shortern_path(filename):30} {url}")
		if not found:
			print("None")

@mirror.command(aliases=["update", "u"])
def update_files():
	with conn:
		for filename, url, archive_filename in cursor.execute(
			"SELECT filename, url, archive_filename FROM mirrors"
		):
			print(f"Updating {shortern_path(filename)} with {url}")
			try:
				download_file(url, filename, archive_filename, exist_ok=True)
			except (ValueError, FileExistsError) as e:
				raise ClickException(str(e)) from e
	print("Updated!")

@mirror.command(aliases=["rm", "r"])
@click.argument("filename")
@click.option("--glob", "-g", is_flag=True)
def remove_file(filename: str, glob: bool):
	print(f"Deleting {shortern_path(filename)}")
	if not glob and not file_in_db(Path(filename)):
		raise ValueError(f"File {shortern_path(filename)} not in database")
	
	with conn:
		if glob:
			conn.execute("DELETE FROM mirrors WHERE filename GLOB ?", (filename, ))
		else:
			conn.execute("DELETE FROM mirrors WHERE filename = ?", (filename, ))
	print(f"Deleted {shortern_path(filename)}")

@mirror.command()
def delete_db():
	if click.confirm('Are you sure you want to delete the database?'):
		shutil.rmtree(SAVE_DIR)
		DB_PATH.unlink()

def download_file(
	url: str, filename: Union[str, Path], archive_filename=None, exist_ok: bool = False
) -> Path:
	resp = requests.get(url)
	resp.raise_for_status()
	#get the filename from the response
	if "Content-Disposition" in resp.headers:
		resp_filename = SAVE_DIR.joinpath("a").with_name(
			cgi.parse_header(resp.headers["Content-Disposition"])[1]["filename"]
		)
	else:
		resp_filename = SAVE_DIR.joinpath(urlparse(url).path.split("/")[-1])
	
	if filename is None:
		if archive_filename is not None:
			filename = SAVE_DIR.joinpath(archive_filename)
		else:
			filename = resp_filename
	else:
		filename = Path(filename)
	#check if file exists in db
	if filename.exists() and not exist_ok:
		if file_in_db(filename):
			raise ValueError(f"File {shortern_path(filename)} already in database")
		else:
			raise FileExistsError(f"File {shortern_path(filename)} already exists")
	if archive_filename is None:
		with open(filename, "wb") as f:
			f.write(resp.content)
	else:
		# archive handling
		# uses partition on .name instead of .suffix due to .tar.gz
		with NamedTemporaryFile(suffix=resp_filename.name.partition(".")[2]) as f:
			with TemporaryDirectory() as tmp_dir:
				f.write(resp.content)
				print(f.name)
				shutil.unpack_archive(f.name, tmp_dir)
				shutil.copyfile(Path(tmp_dir).joinpath(archive_filename), filename)
	return filename

def file_in_db(filename: Path) -> bool:
	if filename.exists():
		with conn:
			cursor.execute(
				"SELECT COUNT(filename) FROM mirrors WHERE filename = ?", (str(filename), )
			)
			return cursor.fetchone()[0] > 0
	return False

def shortern_path(filename: Union[str, Path]) -> str:
	#shorten /home/user/ to ~
	filename = Path(filename)
	try:
		return "~/" + str(filename.relative_to(Path.home()))
	except ValueError:
		return str(filename)

if __name__ == "__main__":
	main()
