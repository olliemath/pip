import logging
import shutil
import sys
import textwrap
import xmlrpc.client
from collections import OrderedDict
from optparse import Values
from typing import TYPE_CHECKING, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from pip._vendor.packaging.version import parse as parse_version
from pip._vendor import html5lib
from pip._vendor import requests
# import html5lib
# import requests


from pip._internal.cli.base_command import Command
from pip._internal.cli.req_command import SessionCommandMixin
from pip._internal.cli.status_codes import NO_MATCHES_FOUND, SUCCESS
from pip._internal.exceptions import CommandError
from pip._internal.metadata import get_default_environment
from pip._internal.models.index import PyPI
from pip._internal.network.xmlrpc import PipXmlrpcTransport
from pip._internal.utils.logging import indent_log
from pip._internal.utils.misc import write_output

if TYPE_CHECKING:
    from typing import TypedDict

    class TransformedHit(TypedDict):
        name: str
        summary: str
        versions: List[str]

logger = logging.getLogger(__name__)


class SearchCommand(Command, SessionCommandMixin):
    """Search for PyPI packages whose name or summary contains <query>."""

    usage = """
      %prog [options] <query>"""
    ignore_require_venv = True

    def add_options(self):
        # type: () -> None
        self.cmd_opts.add_option(
            '-i', '--index',
            dest='index',
            metavar='URL',
            default=PyPI.pypi_url,
            help='Base URL of Python Package Index (default %default)')

        self.parser.insert_option_group(0, self.cmd_opts)

    def run(self, options, args):
        # type: (Values, List[str]) -> int
        if not args:
            raise CommandError('Missing required argument (search query).')
        query = args
        pypi_hits = self.search(query, options)
        hits = transform_hits(pypi_hits)

        terminal_width = None
        if sys.stdout.isatty():
            terminal_width = shutil.get_terminal_size()[0]

        print_results(hits, terminal_width=terminal_width)
        if pypi_hits:
            return SUCCESS
        return NO_MATCHES_FOUND

    def search(self, query, options):
        # type: (List[str], Values) -> List[Dict[str, str]]
        index_url = options.index
        return webpage_search(index_url, query)


def webpage_search(base_url, query):
    # type: (str, str) -> List[Dict[str, str]]
    """Use the search page of pip to return search results.

    The need for this is caused by the XMLRPC search endpoint being disabled.
    """
    results = []
    url = urlunparse(urlparse(base_url)._replace(path="/search"))
    response = requests.get(url, params={"q": query})
    response.raise_for_status()
    content = html5lib.html5parser.parse(
        response.content, namespaceHTMLElements=False
    )
    for result in content.findall(".//*[@class='package-snippet']"):
        name = result.find("h3/*[@class='package-snippet__name']").text
        version = result.find("h3/*[@class='package-snippet__version']").text
        if not name or not version:
            continue

        description = result.find(
            "p[@class='package-snippet__description']"
        ).text

        results.append(dict(name=name, version=version, summary=description))

    return results


def transform_hits(hits):
    # type: (List[Dict[str, str]]) -> List[TransformedHit]
    """
    The list from pypi is really a list of versions. We want a list of
    packages with the list of versions stored inline. This converts the
    list from pypi into one we can use.
    """
    packages = OrderedDict()  # type: OrderedDict[str, TransformedHit]
    for hit in hits:
        name = hit['name']
        summary = hit['summary']
        version = hit['version']

        if name not in packages.keys():
            packages[name] = {
                'name': name,
                'summary': summary,
                'versions': [version],
            }
        else:
            packages[name]['versions'].append(version)

            # if this is the highest version, replace summary and score
            if version == highest_version(packages[name]['versions']):
                packages[name]['summary'] = summary

    return list(packages.values())


def print_results(hits, name_column_width=None, terminal_width=None):
    # type: (List[TransformedHit], Optional[int], Optional[int]) -> None
    if not hits:
        return
    if name_column_width is None:
        name_column_width = max([
            len(hit['name']) + len(highest_version(hit.get('versions', ['-'])))
            for hit in hits
        ]) + 4

    env = get_default_environment()
    for hit in hits:
        name = hit['name']
        summary = hit['summary'] or ''
        latest = highest_version(hit.get('versions', ['-']))
        if terminal_width is not None:
            target_width = terminal_width - name_column_width - 5
            if target_width > 10:
                # wrap and indent summary to fit terminal
                summary_lines = textwrap.wrap(summary, target_width)
                summary = ('\n' + ' ' * (name_column_width + 3)).join(
                    summary_lines)

        name_latest = f'{name} ({latest})'
        line = f'{name_latest:{name_column_width}} - {summary}'
        try:
            write_output(line)
            dist = env.get_distribution(name)
            if dist is not None:
                with indent_log():
                    if dist.version == latest:
                        write_output('INSTALLED: %s (latest)', dist.version)
                    else:
                        write_output('INSTALLED: %s', dist.version)
                        if parse_version(latest).pre:
                            write_output('LATEST:    %s (pre-release; install'
                                         ' with "pip install --pre")', latest)
                        else:
                            write_output('LATEST:    %s', latest)
        except UnicodeEncodeError:
            pass


def highest_version(versions):
    # type: (List[str]) -> str
    return max(versions, key=parse_version)
