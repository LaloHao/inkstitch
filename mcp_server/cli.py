#!/usr/bin/env python3
# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Thin CLI over lib/design_query.py, for testing the MCP connector's core
logic without going through an MCP client.

    python -m mcp_server.cli summarize some_design.svg
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click  # noqa: E402

from lib import design_query, mcp_registry  # noqa: E402


def _print(data):
    click.echo(json.dumps(data, indent=2))


@click.group()
def cli():
    pass


@cli.command()
def open_designs():
    """List designs Ink/Stitch has recently seen open in Inkscape."""
    _print(mcp_registry.list_registered_designs())


@cli.command()
@click.argument('svg_path')
def summarize(svg_path):
    design = design_query.load_design(svg_path)
    _print(design_query.summarize_design(design))


@cli.command(name='list-elements')
@click.argument('svg_path')
@click.option('--element-id', default=None)
def list_elements(svg_path, element_id):
    design = design_query.load_design(svg_path)
    _print(design_query.list_element_params(design, element_id))


@cli.command()
@click.argument('svg_path')
@click.option('--no-warnings', is_flag=True)
def diagnose(svg_path, no_warnings):
    design = design_query.load_design(svg_path)
    _print(design_query.diagnose_design(design, include_warnings=not no_warnings))


@cli.command(name='stitch-plan-stats')
@click.argument('svg_path')
def stitch_plan_stats(svg_path):
    design = design_query.load_design(svg_path)
    _print(design_query.stitch_plan_stats(design))


if __name__ == '__main__':
    cli()
