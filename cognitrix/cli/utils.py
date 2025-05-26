"""
CLI utility functions for common operations.
"""
import os
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

def print_table(rows, headers):
    """Prints a table given rows (list of lists) and headers (list)."""
    if not rows:
        print("\nNo data found.")
        return
    col_widths = [max(len(str(cell)) for cell in col) for col in zip(*([headers] + rows))]
    fmt = '| ' + ' | '.join(f'{{:<{w}}}' for w in col_widths) + ' |'
    sep = '|-' + '-|-'.join('-' * w for w in col_widths) + '-|'
    print('\n' + fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*row))
    print()

def str_or_file(string):
    """Returns string content or file content if string is a file path."""
    if len(string) > 100:
        return string
    if Path(string).is_file() or Path(os.curdir, string).is_file():
        with open(Path(string), 'rt') as file:
            return file.read()
    return string

def create_rich_table(headers, rows, title=None):
    """Creates a Rich table for better display."""
    table = Table(title=title)
    for header in headers:
        table.add_column(header, style="bold")
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table 