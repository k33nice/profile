#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Smart parser for IceWM's address bar feature

Run without arguments for usage.

Included (somewhat slapdash but fairly comprehensive) test suite may be run
with `nosetests <name of this file>`.

@todo: Refactor to allow cleaner, more thorough unit tests.
@todo: Use notify_error to display exceptions.
@todo: Make sure the URL handling is robust in the face of lazy-escaped URLs.
@todo: Add support for using a config file of some sort (.gmrunrc?) to specify
    URL handlers directly since xdg-open is often configured to simply hand all
    URLs off to the user's web browser. This line (once de-obfuscated) does
    everything necessary for parsing except variable resolution:
    [[y.strip() for y in x.split('=')] for x in open('.gmrunrc','rU') if x.strip() and not x.strip().startswith('#')]
"""

__appname__ = "Smart Address Bar"
__author__  = "Stephan Sokolow (deitarion/SSokolow)"
__version__ = "0.1"
__license__ = "MIT"

import logging, os, re, shlex, string, subprocess, sys, time
from xml.sax.saxutils import escape as xmlescape

#{ Configuration

OPEN_CMD = ['xdg-open']
BROWSE_CMD = ['pcmanfm']
TERMINAL_CMD = ['xterm', '-hold', '-e']

NOTIFY_BACKENDS = [
        ['notify-send', '%(title)s', '%(text_xml)s'],
        ['zenity', '--title', '%(title)s', '--text', '%(text_xml)s'],
        ['xmessage', '-default', 'okay', '%(title):\n%(text)s']]

#{ Helper Functions

uri_re = re.compile(r"""^[a-zA-Z0-9+.\-]+:.+$""", re.IGNORECASE | re.UNICODE)

# Set up proper commandline split/unsplit for the platform in question
if os.name == 'nt':
    #TODO: Figure out how to split on Windows.
    _split = shlex.split
    _unsplit = subprocess.list2cmdline
    _useterm = lambda : True
else:
    _split = shlex.split
    _unsplit = lambda args: ' '.join([sh_quote(x) for x in args])
    _useterm = lambda : getattr(sys.stdout, 'fileno', None) and not os.isatty(sys.stdout.fileno())

def sh_quote(file):
    """Reliably quote a string as a single argument for /bin/sh

    Borrowed from the pipes module in Python 2.6.2 stdlib and fixed to quote
    empty strings properly and pass completely safechars strings through.
    """
    _safechars = string.ascii_letters + string.digits + '!@%_-+=:,./' # Safe unquoted
    _funnychars = '"`$\\'                           # Unsafe inside "double quotes"

    if not file:
        return "''"

    for c in file:
        if c not in _safechars:
            break
    else:
        return file

    if not [x for x in file if x not in _safechars]:
        return file
    elif '\'' not in file:
        return '\'' + file + '\''

    res = ''
    for c in file:
        if c in _funnychars:
            c = '\\' + c
        res = res + c
    return '"' + res + '"'

def which(name, flags=os.X_OK):
    """Search PATH for executable files with the given name.

    On newer versions of MS-Windows, the PATHEXT environment variable will be
    set to the list of file extensions for files considered executable. This
    will normally include things like ".EXE". This fuction will also find files
    with the given name ending with any of these extensions.

    On MS-Windows the only flag that has any meaning is os.F_OK. Any other
    flags will be ignored.

    Changes from Original:
    - Don't return matches for directories.
    - Call expanduser() on "name" too.

    @type name: C{str}
    @param name: The name for which to search.

    @type flags: C{int}
    @param flags: Arguments to L{os.access}.

    @rtype: C{list}
    @param: A list of the full paths to files found, in the
    order in which they were found.

    Source: twisted/python/procutils.py (from Twisted 2.4.0)
    Copyright (c) 2001-2004 Twisted Matrix Laboratories.
    Copyright (c) 2007, 2011 Stephan Sokolow.
    License: MIT
    See LICENSE from the Twisted 2.4.0 tarball for details.
    """
    result = []
    exts = filter(None, os.environ.get('PATHEXT', '').split(os.pathsep))

    for bindir in os.environ['PATH'].split(os.pathsep):
        name = os.path.expanduser(name)
        binpath = os.path.join(os.path.expanduser(bindir), name)
        if os.access(binpath, flags) and os.path.isfile(binpath):
            result.append(binpath)
        for ext in exts:
            pext = binpath + ext
            if os.access(pext, flags) and os.path.isfile(pext):
                result.append(pext)
    return result

def notify_error(text, title='Alert'):
    """Error-display wrapper for various notification/dialog backends.

    @todo: Add support for HTML notifications.
    @todo: Generalize for different notification types.
    @todo: Add support for using D-Bus directly.
    @todo: Add KDE detection and kdialog support.
    """
    params = {
            'title'   : title,
            'text'    : str(text),
            'text_xml': xmlescape(str(text))
            }

    for backend in NOTIFY_BACKENDS:
        if which(backend[0]):
            try:
                subprocess.check_call([x % params for x in backend])
            except subprocess.CalledProcessError:
                continue
            else:
                break
    else:
        # Just in case it's run from a console-only shell.
        print "%s:" % title
        print text

#{ Main Functionality

def execute(args):
    """Wrapper to abstract the behavior of os.exec* away.

    (and allow easy experimentation with alternate behaviours)
    """
    os.execvp(args[0], args)
    sys.exit(3)

def call_timeout(cmd, *args, **kwargs):
    """check_call-like wrapper which times out after 30 seconds.

    (So this process doesn't lie around wasting resources needlessly but
    CalledProcessException can still be thrown for bad commands.)
    """
    start = time.time()
    proc = subprocess.Popen(cmd, *args, **kwargs)

    while proc.poll() is None and time.time() - start < 30:
        time.sleep(1)

    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    else:
        return proc.returncode

def run(args):
    """Heuristically guess how to handle the given commandline."""

    # Prefer the user's chosen shell for shell execute.
    if os.name == 'posix':
        shell_cmd = os.environ.get('SHELL', '/bin/sh')
    elif os.name == 'nt':
        shell_cmd = os.environ.get('COMSPEC', None)
    else:
        shell_cmd = None # Fall back to autodetection

    # Be easy-going about input for user convenience.
    if not args:
        return False
    elif isinstance(args, basestring):
        args = [args]

    if len(args) == 1:
        # May be unparsed, may just be unquoted.
        longcmd, argv = args[0], _split(args[0])
    else:
        # Either ready to use or unquoted.
        longcmd, argv = _unsplit(args), args

    # Flexible quoting for maximum versatility. (Order minimizes mistakes)
    for cmd in (longcmd, os.path.expanduser(argv[0])):
        if which(cmd):
            # Valid command (shell execute for versatility)
            logging.info("Running as command: %r" % argv)
            execute([shell_cmd, '-c', longcmd])
        elif os.path.isdir(cmd):
            # Local Directory (open in file manager)
            logging.info("Opening directory: %s" % cmd)
            execute(BROWSE_CMD + [cmd])
        elif os.path.isfile(cmd):
            # Local File (open in desired application)
            logging.info("Opening file: %s" % cmd)
            execute(OPEN_CMD + [cmd])
        elif cmd.startswith('file://'):
            # Local path as URL (let file manager decide)
            execute(BROWSE_CMD + [cmd])
        elif uri_re.match(cmd):
            # Likely URI (open in desired URL handler)
            logging.info("Opening URL: %s" % cmd)
            execute(OPEN_CMD + [cmd])
        else:
            continue # No match, try the alternate interpretation.
        break # Match found, don't try the alternate interpretation.
    else:
        # Fall back to letting the shell try to make sense of it.
        try:
            logging.info("Attempting shell fallback: %r" % argv)
            if _useterm():
                call_timeout(TERMINAL_CMD + [shell_cmd, '-c', longcmd])
            else:
                call_timeout(longcmd, shell=True, executable=shell_cmd)
        except subprocess.CalledProcessError:
            notify_error(repr(argv), title="Command not found")
            notify_error(repr(longcmd), title="Command not found")
            return False
    return True # Used for automated testing

#{ Test Routines for python-nose

def _check_core(args, all_mocks=False):
    mok = all_mocks and '--use-all-mocks' or '--use-mocks'
    return subprocess.call([__file__, mok, '--'] + args,
            stdout = open(os.devnull, 'w'),
            stderr = subprocess.STDOUT)

def _check_run(args): assert _check_core(args) == 0, repr(args)
def _check_run_fail(args): assert _check_core(args) != 0, repr(args)
def _check_run_term(args): assert _check_core(args, True) == 0, repr(args)

def test_commands():
    """Testing simple commands"""
    os.environ['TEST_ECHO'] = 'echo'
    for cmd in ('echo', '/bin/echo', 'echo Success "" ...verily', "$TEST_ECHO"):
        for variant in ([cmd], shlex.split(cmd)):
            yield _check_run, variant
            yield _check_run_term, variant

def test_builtins():
    """Testing shell script builtins"""
    cmd = 'for X in Success1 Success2; do echo $X "echo in for loop" > /dev/null; done'
    yield _check_run, [cmd]

def test_paths():
    """Testing non-command paths (files and directories) and URLs"""
    for cmd in ('/etc', '/etc/resolv.conf', 'file:///etc/resolv.conf', 'http://www.example.com'):
        for variant in ([cmd], shlex.split(cmd)):
            yield _check_run, variant

def test_spaced_paths():
    """Testing non-command paths (files and directories) and URLs with spaces"""
    import shutil, tempfile, urllib
    tmp_path = tempfile.mkdtemp()
    try:
        test_dir = os.path.join(tmp_path, 'Foo Bar')
        test_file = os.path.join(test_dir, 'baz.html')

        os.mkdir(test_dir)
        open(test_file, 'w').close()

        for cmd in (
                test_dir, test_file,
                'file://' + test_dir, 'file://' + test_file,
                'file://' + urllib.pathname2url(test_dir),
                'file://' + urllib.pathname2url(test_file),
                'http://127.0.0.1' + test_dir):
            for variant in ([cmd], shlex.split(cmd)):
                yield _check_run, variant
    finally:
        shutil.rmtree(tmp_path)

def test_failure():
    """Testing failure conditions"""
    yield _check_run_fail, []

    for nonsense in ('rijwoigjrwo', 'for foo in', 'for foo in x y; done'):
        for variant in ([nonsense], shlex.split(nonsense)):
            yield _check_run_fail, variant

    os.environ['TEST_ECHO'] = 'ecoh'
    yield _check_run_fail, ['$TEST_ECHO']
#}

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    if len(sys.argv) < 2:
        notify_error((
                "%(cmd)s <cmd> [arg] ...\n" +
                "  or  \n" +
                "%(cmd)s <cmd with args>") % {'cmd': os.path.basename(sys.argv[0])}
                , title="Usage")
        sys.exit(2)
    else:
        args = sys.argv[1:]
        use_mocks = False

        if args[0:2] == ['--use-all-mocks', '--']:
            _useterm = lambda: True

        if args[0:2] in (['--use-mocks', '--'], ['--use-all-mocks', '--']):
            args = args[2:]

            BROWSE_CMD = ['echo', 'pcmanfm']
            OPEN_CMD = ['echo', 'xdg-open']
            TERMINAL_CMD = ['env']
            use_mocks = True

        outcome = run(args)

        # Exit with a return code of 1 on failure
        sys.exit(int(not outcome))
