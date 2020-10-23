import shutil
import sqlite3
from pathlib import Path
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
	new_db = not DB_PATH.exists()
	DB_PATH.touch(exist_ok=True)
	conn = sqlite3.connect(DB_PATH)
	cursor = conn.cursor()
	if new_db:
		with conn:
			cursor.execute("CREATE TABLE mirrors (filename text, url text)")
			#cursor.execute("CREATE TABLE release_mirrors (filename text, repo text, regex text)")
	mirror()
	conn.close()

@click.group(context_settings={"help_option_names": ["-h", "--help"]}, cls=ClickAliasedGroup)
def mirror():
	pass

@mirror.command(aliases=["addf", "add", "a"])
@click.argument("url")
@click.option("--filename", "-f")
@click.option("--mode", "-m", type=OCTAL_PARAM, default="755", show_default=True)
def add_file(url, filename, mode):
	try:
		filename = download_file(url, filename)
	except (ValueError, FileExistsError) as e:
		raise ClickException(str(e))
	filename.chmod(mode)
	with conn:
		cursor.execute("INSERT INTO mirrors VALUES(?, ?)", (str(filename), url))
	print(f"Added {url} at {filename}")

"""
@mirror.command(aliases=["addr", "a"])
@click.argument("repo")
@click.option("--filename", "-f")
@click.option("--regex", "-r")
def add_release(repo, filename):
	repo = github.get_repo(repo)
	assets = repo.get_latest_release().get_assets()
	if not regex:
		regex = r""
	for asset in assets:
		if 
"""

@mirror.command(aliases=["list", "ls", "l"])
def list_files():
	with conn:
		print("URL Mirrors:")
		mirrors = cursor.execute("SELECT filename, url FROM mirrors")
		found = False
		for filename, url in mirrors:
			found = True
			print(f"{filename:30}\t{url:>30}")
		if not found:
			print("None")
		"""print("\nRepo Mirrors:")
		mirrors = cursor.execute("SELECT filename, repo FROM release_mirrors")
		found = False
		for filename, url in mirrors:
			found = True
			print(f"{filename:15}\t{url}")
		if not found:
			print("None")
		"""

@mirror.command(aliases=["update", "u"])
def update_files():
	with conn:
		for filename, url in cursor.execute("SELECT filename, url FROM mirrors"):
			print(f"Updating {filename} with {url}")
			try:
				download_file(url, filename, exist_ok=True)
			except (ValueError, FileExistsError) as e:
				raise ClickException(str(e)) from e
	print("Updated!")

@mirror.command(aliases=["rm", "r"])
@click.argument("filename")
@click.option("--glob", "-g", is_flag=True)
def remove_file(filename, glob):
	if not glob and not file_in_db(filename):
		raise ValueError(f"File {filename} not in database")
	
	with conn:
		if glob:
			conn.execute("DELETE FROM mirrors WHERE filename GLOB ?", (filename, ))
		else:
			conn.execute("DELETE FROM mirrors WHERE filename = ?", (filename, ))
	print(f"Deleted {filename}")

@mirror.command()
def delete_db():
	if click.confirm('Are you sure you want to delete the database?'):
		shutil.rmtree(SAVE_DIR)
		DB_PATH.unlink()

def download_file(url, filename, exist_ok=False):
	resp = requests.get(url)
	resp.raise_for_status()
	if filename is None:
		if "Content-Disposition" in resp.headers:
			filename = SAVE_DIR.joinpath("a").with_name(resp.headers["Content-Disposition"])
		else:
			filename = SAVE_DIR.joinpath(urlparse(url).path.split("/")[-1])
	else:
		filename = Path(filename)
	if filename.exists() and not exist_ok:
		if file_in_db(filename):
			raise ValueError(f"File {filename} already in database")
		else:
			raise FileExistsError(f"File {filename} already exists")
	with open(filename, "wb") as f:
		f.write(resp.content)
	return filename

def file_in_db(filename):
	if filename.exists():
		with conn:
			cursor.execute(
				"SELECT COUNT(filename) FROM mirrors WHERE filename = ?", (str(filename), )
			)
			return cursor.rowcount != -1
	return False

if __name__ == "__main__":
	main()
