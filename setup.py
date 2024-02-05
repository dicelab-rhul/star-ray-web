from setuptools import setup, find_packages

setup(
    name="star_ray_web",
    version="0.0.1",
    author="Benedict Wilkins",
    author_email="benrjw@gmail.com",
    description="",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(),
    install_requires=[
        "ray[serve]>=0.5",
    ],
    package_data={
        "": ["*.js"],
        "star_ray_web": ["static/js/*.js", "static/svg/*.svg", "templates/*.html"],
    },
    python_requires=">=3.10",
)
