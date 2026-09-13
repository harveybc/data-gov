from setuptools import find_packages, setup

setup(
    name="data-gov",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    package_data={"web_plugins": ["templates/*.html"]},
    entry_points={
        "console_scripts": [
            "data-gov=app.main:main",
        ],
        "datagov.pipeline": [
            "default_pipeline=pipeline_plugins.default_pipeline:Plugin",
        ],
        "datagov.web": [
            "default_web=web_plugins.default_web:Plugin",
        ],
        "datagov.access": [
            "default_access=access_plugins.default_access:Plugin",
        ],
        "datagov.accounting": [
            "default_accounting=accounting_plugins.default_accounting:Plugin",
        ],
        "datagov.lake": [
            "default_lake=lake_plugins.default_lake:Plugin",
            "files_lake=lake_plugins.files_lake:Plugin",
            "sql_lake=lake_plugins.sql_lake:Plugin",
        ],
        "datagov.role": [
            "default_role=role_plugins.default_role:Plugin",
        ],
    },
    install_requires=[
        "flask>=3.0",
        "pandas>=2.0",
        "pyarrow>=14.0",
    ],
    author="Harvey Bastidas",
    description=(
        "Data governance for multiple remote data lakes: inventory, automatic "
        "policy, accounting, and event-driven roles. Not a cloud catalog."
    ),
)
