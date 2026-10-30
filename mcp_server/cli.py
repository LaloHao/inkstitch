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

from lib import design_query, live_edit, mcp_registry  # noqa: E402


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
@click.option('--all-params', is_flag=True, help="Include params left at their default.")
@click.option('--param', 'param_names', multiple=True, help="Only list this param (repeatable).")
def list_elements(svg_path, element_id, all_params, param_names):
    design = design_query.load_design(svg_path)
    _print(design_query.list_element_params(design, element_id, not all_params, list(param_names) or None))


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


@cli.command(name='measure-elements')
@click.argument('svg_path')
@click.option('--element-id', 'element_ids', multiple=True, help="Only measure this element (repeatable).")
def measure_elements(svg_path, element_ids):
    design = design_query.load_design(svg_path)
    _print(design_query.measure_elements(design, list(element_ids) or None))


@cli.command(name='suggest-trims')
@click.argument('svg_path')
@click.option('--min-gap-mm', default=1.0, show_default=True)
def suggest_trims(svg_path, min_gap_mm):
    design = design_query.load_design(svg_path)
    _print(design_query.suggest_trims(design, min_gap_mm))


@cli.command(name='set-params')
@click.argument('svg_path')
@click.argument('changes_json')
def set_params(svg_path, changes_json):
    """Live-edit params in Inkscape and verify. CHANGES_JSON is e.g.
    '[{"ids": ["path1"], "params": {"zigzag_spacing_mm": "0.4"}}]'."""
    _print(live_edit.apply_param_changes(svg_path, json.loads(changes_json)))


if __name__ == '__main__':
    cli()
