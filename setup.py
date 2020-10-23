import setuptools
with open("README.md", "r") as f:
	long_description = f.read()
setuptools.setup(
	name="mirror",
	version="0.1",
	descripton="mirrors remote files using HTTP",
	long_description=long_description,
	long_description_content_type="text/markdown",
	packages=["mirror"],
	license="MIT",
	author="SuperStormer",
	author_email="larry.p.xue@gmail.com",
	url="https://github.com/SuperStormer/mirror",
	project_urls={"Source Code": "https://github.com/SuperStormer/mirror"},
	entry_points={"console_scripts": ["mirror=mirror:main"]},
	install_requires=["requests>=2.14.0", "click>=7.1.2", "click-aliases>=1.0.1"]
)
