# ------------------------------------------------------------------------------
# CodeHawk C Analyzer
# Author: Henny Sipma
# ------------------------------------------------------------------------------
# The MIT License (MIT)
#
# Copyright (c) 2017-2020 Kestrel Technology LLC
# Copyright (c) 2020-2022 Henny B. Sipma
# Copyright (c) 2023-2024 Aarno Labs LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ------------------------------------------------------------------------------
"""File utilities

All intermediate and final results are saved in xml/json files with fixed
names derived from the names of the c files and c functions. The functions in
this file parse the xml/json files and return the top functional xml element
of these files (xml) or the dictionary (json). The filenames themselves can
be retrieved as well.

File-naming schema:

A file x.c in a project is identified by three components:

- a project path <pp>, which can be either the directory of the file
  (in case of a single file) or the location of the Makefile (in
  case of a multi file project);
- a project name <pn>, which is the x in the case of a single c file,
  and is user-specified name otherwise;
- a file path <fp>, which is the path of the directory in which the
  file resides relative to the project path. In case of a single
  c file fp is omitted.
- the file name <x> (without extension)

The user can specify a different directory to store the analysis artifacts,
which is designated by <tp> (targetpath).

Given a file x.c with project path pp, project name pn, and file path fp,
and specified target path tp, the following artifacts are produced in
multiple stages:

1. A new analysis directory is created: tp/pn.cch with two subdirectories:
   tp/pn.cch/a and tp/pn.cch/s

2. x.c is preprocessed with the gcc preprocessor, producing the file
   pp/fp/x.i. Both pp/fp/x.c and pp/fp/x.i are copied to tp/pn.cch/s/fp.

3. x.c is parsed by the CodeHawk/CIL parser, producing the following files

   - tp/pn.cch/a/fp/x/x_cdict.xml
   - tp/pn.cch/a/fp/x/x_cfile.xml
   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_cfun.xml
     (for every function fn in x)

3a. In case of multiple c files, the files are linked, resolving dependencies
    between globally visible functions, variables, and data structures,
    producing the files:

    - tp/pn.cch/a/fp/x/x_gxrefs.xml
    - tp/pn.cch/a/globaldefinitions.xml
    - tp/pn.cch/a/target_files.xml

4. When all c files in the project (that is, indicated by the Makefile)
   have been parsed the directory tp/pn.cch is saved as a gzipped tar file,
   tp/pn.cch.tar.gz, to enable redoing the analysis without having to
   reparse, or sharing the same parsing results with others (this is
   because the preprocessing is performed against the local environment,
   including header files, availability of other libraries, etc., and
   thus may be different for different computers).

5. Primary proof obligations are generated for x.c, producing the following
   files:

   - tp/pn.cch/a/fp/x/x_cgl.xml
   - tp/pn.cch/a/fp/x/x_ctxt.xml
   - tp/pn.cch/a/fp/x/x_ixf.xml
   - tp/pn.cch/a/fp/x/x_prd.xml
   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_api.xml
   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_pod.xml
   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_ppo.xml
   - tp/pn.cch/a/fp/x/logfiles/x_primary.chlog
   - tp/pn.cch/a/fp/x/logfiles/x_primary.errorlog
   - tp/pn.cch/a/fp/x/logfiles/x_primary.infolog

6. Invariants are generated and proof obligations are checked, producing
   the following files:

   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_invs.xml
   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_vars.xml
   - tp/pn.cch/a/fp/x/logfiles/x_gencheck.chlog
   - tp/pn.cch/a/fp/x/logfiles/x_gencheck.errorlog
   - tp/pn.cch/a/fp/x/logfiles/x_gencheck.infolog

7. Supporting proof obligations are generated for dependencies across
   functions and files, producing

   - tp/pn.cch/a/fp/x/functions/<x_fn>/x_fn_spo.xml

Steps 6 and 7 are repeated until convergence.

"""
import calendar
import json
import os
import subprocess
import shutil
import time
import xml.etree.ElementTree as ET

from typing import Any, cast, Dict, List, Optional, Tuple, TYPE_CHECKING

import chc.util.xmlutil as UX

from chc.util.Config import Config
from chc.util.loggingutil import chklogger

if TYPE_CHECKING:
    from chc.app.CFile import CFile


config = Config()


class CHError(Exception):

    def __init__(self, msg: str) -> None:
        Exception.__init__(self, msg)
        self.msg = msg

    def wrap(self) -> str:
        lines = []
        lines.append("*" * 80)
        lines.append(self.__str__())
        lines.append("*" * 80)
        return "\n".join(lines)


class CHCError(CHError):
    def __init__(self, msg: str) -> None:
        CHError.__init__(self, msg)


class CHCParserNotFoundError(CHCError):
    def __init__(self, location: str) -> None:
        CHCError.__init__(self, "CodeHawk C Parser not found at " + location)


class CHCAnalyzerNotFoundError(CHCError):
    def __init__(self, location: str) -> None:
        CHCError.__init__(
            self, "CodeHawk C Analyzer executable not found at " + location
        )


class CHCGuiNotFoundError(CHCError):
    def __init__(self, location: Optional[str]) -> None:
        CHCError.__init__(self, "CodeHawk C Analyzer Gui not found")
        self.location = location

    def __str__(self) -> str:
        if self.location is None:
            return (
                "Location of the CodeHawk C Gui has not been set in ConfigLocal.\n"
                + " Please assign the location of the gui executable as "
                + " config.chc_gui in util/ConfigLocal.py"
            )
        else:
            return "CodeHawk C Gui executable not found at " + self.location


class CHCFileNotFoundError(CHCError):
    def __init__(self, filename: str) -> None:
        CHCError.__init__(self, "File " + filename + " not found")
        self.filename = filename


class CFileNotFoundException(CHCError):
    def __init__(self, filenames: List[str]) -> None:
        CHCError.__init__(self, "Files " + " ".join(filenames) + " not found")


class CHCTargetGroupNotFoundError(CHCError):
    def __init__(self, group: str) -> None:
        CHCError.__init__(self, "Groupname " + group + " not found in config.targets")
        self.group = group


class CHCTargetGroupFileNotFoundError(CHCError):
    def __init__(self, filename: str) -> None:
        CHCError.__init__(self, "Group file: " + filename + " not found")
        self.filename = filename


class CHCShortCutNameError(CHCError):
    def __init__(self, name: str) -> None:
        CHCError.__init__(self, "Name: " + name + " is not a valid short-cut name")
        self.name = name


class CHCProjectNameNotFoundError(CHCError):
    def __init__(self, group: str, name: str, projects: List[str]) -> None:
        CHCError.__init__(self, "Project name not found: " + name)
        self.group = group
        self.name = name
        self.projects = projects

    def __str__(self) -> str:
        msg = (
            "Project name: "
            + self.name
            + " not found in file for "
            + self.group
            + "\nProjects found:\n"
            + ("-" * 80)
            + "".join(["\n  - " + p for p in self.projects])
        )
        return msg


class CHCSingleCFileNotFoundError(CHCError):
    def __init__(self, filenames: List[str]) -> None:
        CHCError.__init__(self, "Requested file not found")
        self.filenames = filenames

    def __str__(self) -> str:
        lines = []
        lines.append("Requested file not found; filenames available: ")
        lines.append("-" * 80)
        for n in self.filenames:
            lines.append("  " + n)
        return "\n".join(lines)


class CHCDirectoryNotFoundError(CHCError):
    def __init__(self, dirname: str) -> None:
        CHCError.__init__(self, "Directory " + dirname + " not found")
        self.dirname = dirname


class CHCSemanticsNotFoundError(CHCError):
    def __init__(self, path: str) -> None:
        CHCError.__init__(self, "No semantics directory or tar file found in " + path)
        self.dirname = path

    def __str__(self) -> str:
        return (
            "Expected to find a semantics directory or semantics tar file in "
            + self.dirname
            + ".\nPlease first parse the c file to produce "
            + "the semantics file/directory"
        )


class CHCArtifactsNotFoundError(CHCError):
    def __init__(self, path: str) -> None:
        CHCError.__init__(self, "Artifacts directory not found in " + path)
        self.path = path

    def __str__(self) -> str:
        return (
            "Directory "
            + self.path
            + " is expected to have a directory "
            + "named chcartifacts or ktadvance (legacy)"
        )


class CHCAnalysisResultsNotFoundError(CHCError):
    def __init__(self, path: str) -> None:
        CHCError.__init__(
            self,
            "No analysis results found for: "
            + path
            + "\nPlease analyze project first.",
        )
        self.path = path


class CHCXmlParseError(CHCError):
    def __init__(
        self, filename: str, errorcode: int, position: Tuple[int, int]
    ) -> None:
        CHCError.__init__(self, "Xml parse  error")
        self.filename = filename
        self.errorcode = errorcode
        self.position = position

    def __str__(self) -> str:
        return (
            "XML parse error in "
            + self.filename
            + " (errorcode: "
            + str(self.errorcode)
            + ") at position  "
            + str(self.position)
        )


class CHCJSONParseError(CHCError):
    def __init__(self, filename: str, e: ValueError) -> None:
        CHCError.__init__(self, "JSON parse error")
        self.filename = filename
        self.valueerror = e

    def __str__(self) -> str:
        return (
            "JSON parse error in file: " + self.filename + ": " + str(self.valueerror)
        )


class CHCSummaryTestNotFound(CHCError):
    def __init__(self, header: str, fname: str, fnames: List[str]) -> None:
        CHCError.__init__(self, "Libc summary test file not found")
        self.header = header
        self.fname = fname
        self.fnames = fnames

    def __str__(self) -> str:
        lines = []
        lines.append(
            "Libc summary test "
            + self.fname
            + " not found for header "
            + self.header
        )
        lines.append(
            "Function tests available for header " + self.header + " are:")
        for name in sorted(self.fnames):
            lines.append("  - " + name)
        return "\n".join(lines)


class CHCSummaryHeaderNotFound(CHCError):
    def __init__(self, header: str, headers: List[str]) -> None:
        CHCError.__init__(self, "Libc header not found")
        self.header = header
        self.headers = headers

    def __str__(self) -> str:
        lines = []
        lines.append("Libc header " + self.header + " not found")
        lines.append("Headers available:")
        for h in sorted(self.headers):
            lines.append("  - " + h)
        return "\n".join(lines)


class CHCJulietTestSuiteNotRegisteredError(CHCError):
    def __init__(self) -> None:
        CHCError.__init__(self, "Juliet test suite not registered")

    def __str__(self) -> str:
        lines = []
        lines.append(
            "Juliet Test Suite repository has not been registered "
            + "in ConfigLocal.py"
        )
        lines.append("Please download or clone")
        lines.append(
            "  "
            + "https://github.com/kestreltechnology/CodeHawk-C-Targets-Juliet"
        )
        lines.append(
            "and add the path to juliettestcases.json in ConfigLocal.py")
        return "\n".join(lines)


class CHCJulietTestSuiteFileNotFoundError(CHCFileNotFoundError):
    def __init__(self, filename: str) -> None:
        CHCFileNotFoundError.__init__(self, filename)

    def __str__(self) -> str:
        return (
            CHCFileNotFoundError.__str__(self)
            + "\nPlease check path to the CodeHawk-C-Targets-Juliet repository"
        )


class CHCJulietTargetFileCorruptedError(CHCError):
    def __init__(self, key: str) -> None:
        CHCError.__init__(self, "Expected to find " + key + " juliettestcases.json")


class CHCJulietCWENotFoundError(CHCError):
    def __init__(self, cwe: str, cwes: List[str]) -> None:
        CHCError.__init__(
            self, "Cwe " + cwe + " not found in juliettestcases.json")
        self.cwe = cwe
        self.cwes = cwes

    def __str__(self) -> str:
        lines = []
        lines.append("Cwe " + self.cwe + " not found in juliettestcases.json")
        lines.append("-" * 80)
        lines.append("Cwes found: ")
        for c in sorted(self.cwes):
            lines.append("  " + c)
        return "\n".join(lines)


class CHCJulietTestNotFoundError(CHCError):
    def __init__(self, cwe: str, test: str, tests: List[str]) -> None:
        CHCError.__init__(
            self, "Test case " + test + " not found for cwe " + cwe)
        self.cwe = cwe
        self.test = test
        self.tests = tests

    def __str__(self) -> str:
        lines = []
        lines.append(
            "Test case " + self.test + " not found for cwe " + self.cwe)
        lines.append("-" * 80)
        lines.append("test cases available for " + self.cwe + ":")
        for t in sorted(self.tests):
            lines.append("  " + t)
        return "\n".join(lines)


class CHCJulietScoreKeyNotFoundError(CHCError):
    def __init__(self, cwe: str, test: str) -> None:
        CHCError.__init__(self, "No score key found for " + cwe + " - " + test)
        self.cwe = cwe
        self.test = test


class CHCJulietScoreFileNotFoundError(CHCError):
    def __init__(self, cwe: str, test: str) -> None:
        CHCError.__init__(
            self, "No score file found for " + cwe + " -  " + test)
        self.cwe = cwe
        self.test = test


def get_xnode(
    filename: str, rootnode: str, desc: str, show: bool = True
) -> Optional[ET.Element]:
    if os.path.isfile(filename):
        try:
            tree = ET.parse(filename)
            root = tree.getroot()
            return root.find(rootnode)
        except ET.ParseError as e:
            raise CHCXmlParseError(filename, e.code, e.position)
    elif show:
        raise CHCFileNotFoundError(filename)
    else:
        chklogger.logger.warning(
            "File %s was not found; returning None", filename)
        return None


def create_backup_file(filename: str) -> None:
    if os.path.isfile(filename):
        timestamp = calendar.timegm(time.gmtime())
        backupfilename = filename + "_" + str(timestamp)
        shutil.copy(filename, backupfilename)


# --------------------------------------- check presence of parser and analyzer


def check_analyzer() -> None:
    if not os.path.isfile(config.canalyzer):
        raise CHCAnalyzerNotFoundError(config.canalyzer)


def check_parser() -> None:
    if not os.path.isfile(config.cparser):
        raise CHCParserNotFoundError(config.cparser)


def check_gui() -> None:
    if config.chc_gui is None or not os.path.isfile(config.chc_gui):
        raise CHCGuiNotFoundError(config.chc_gui)


# ------------------------------------------------------------------------------
# Short-cut names
# targettable: group -> project
#
# A project can be specified with a short-cut name of the format
#   <group-name>:<project-name>
# where the group-name is registered in Config.targets with the file name of
# a json file that lists the project-name(s) with potentially additional
# information on those projects (e.g., 32-bit or 64-bit compiled).
# ------------------------------------------------------------------------------


def get_analysis_target_index(group: str) -> Dict[Any, Any]:
    """Returns the dictionary referred to by the group name."""
    filename = config.targets.get(group, None)
    if filename is None:
        raise CHCTargetGroupNotFoundError(group)
    if not os.path.isfile(filename):
        raise CHCTargetGroupFileNotFoundError(filename)
    try:
        with open(filename, "r") as fp:
            d = json.load(fp)
    except ValueError as e:
        raise CHCJSONParseError(filename, e)
    if "targets" in d:
        return d["targets"]
    return {}


def is_shortcut_name(name: str) -> bool:
    """Returns true if the name is a valid short-cut name."""
    return name.count(config.name_separator) == 1


def get_group_name(name: str) -> str:
    """Returns the group-name from a short-cut name."""
    if is_shortcut_name(name):
        return name.split(config.name_separator)[0]
    raise CHCShortCutNameError(name)


def get_project_name(name: str) -> str:
    """Returns the project name from a short-cut name."""
    if is_shortcut_name(name):
        return name.split(config.name_separator)[1]
    raise CHCShortCutNameError(name)


def get_registered_analysis_targets() -> Dict[str, Any]:
    """Returns a dictionary of group -> (path,project-dictionary)."""
    result: Dict[str, Any] = {}
    for groupindex in config.targets:
        result[groupindex] = {}
        result[groupindex]["path"] = os.path.dirname(config.targets[groupindex])
        result[groupindex]["projects"] = get_analysis_target_index(groupindex)
    return result


def get_project_path(name: str) -> str:
    if is_shortcut_name(name):
        group = get_group_name(name)
        if group in config.targets:
            gpath = os.path.dirname(config.targets[group])
            grouptargets = get_analysis_target_index(group)
            projectname = get_project_name(name)
            if projectname in grouptargets:
                projectrec = grouptargets[projectname]
                ppath = projectrec["path"]
                ppath = os.path.join(gpath, ppath)
                if os.path.isdir(ppath):
                    return os.path.join(gpath, ppath)
                else:
                    raise CHCDirectoryNotFoundError(ppath)
            else:
                raise CHCProjectNameNotFoundError(
                    group, projectname, list(grouptargets.keys())
                )
        else:
            raise CHCTargetGroupNotFoundError(group)
    else:
        name = os.path.abspath(name)
        if os.path.isdir(name):
            return name
        else:
            raise CHCDirectoryNotFoundError(name)

# Check presence of analysis results ------------------------------------------


def check_analysis_results(path: str) -> None:
    """Raises an exception if analysis results are not present."""
    filename = os.path.join(path, "summaryresults.json")
    if os.path.isfile(filename):
        return
    raise CHCAnalysisResultsNotFoundError(path)


def check_cfile(path: str, filename: str) -> None:
    filename = os.path.join(path, filename)
    if os.path.isfile(filename):
        return
    raise CHCFileNotFoundError(filename)


def get_chc_artifacts_path(path: str) -> str:
    dirname = os.path.join(path, "a")
    if os.path.isdir(dirname):
        return dirname
    dirname = os.path.join(path, "chcartifacts")  # legacy name
    if os.path.isdir(dirname):
        return dirname
    dirname = os.path.join(path, "ktadvance")  # legacy name
    if os.path.isdir(dirname):
        return dirname
    raise CHCArtifactsNotFoundError(path)


# -- Global paths -----------------------------------------------------------

def get_cchpath(targetpath: str, projectname: str) -> str:
    cchname = projectname + ".cch"
    return os.path.join(targetpath, cchname)


def get_analysisresults_path(targetpath: str, projectname: str) -> str:
    cchpath = get_cchpath(targetpath, projectname)
    return os.path.join(cchpath, "a")


def has_analysisresults_path(targetpath: str, projectname: str) -> bool:
    path = get_analysisresults_path(targetpath, projectname)
    return os.path.isdir(path)


def get_savedsource_path(targetpath: str, projectname: str) -> str:
    cchpath = get_cchpath(targetpath, projectname)
    return os.path.join(cchpath, "s")


def get_parse_archive(targetpath: str, projectname: str) -> str:
    """Returns the full path to the parse archive file."""

    archivename = projectname + ".cch.tar.gz"
    return os.path.join(targetpath, archivename)


def get_parse_tarname(projectname: str) -> str:
    return projectname + ".cch.tar"


def get_parse_targzname(projectname: str) -> str:
    return get_parse_tarname(projectname) + ".gz"


def get_targetfiles_filename(targetpath: str, projectname: str) -> str:
    path = get_analysisresults_path(targetpath, projectname)
    return os.path.join(path, "target_files.xml")


def get_functionindex_filename(targetpath: str, projectname: str) -> str:
    path = get_analysisresults_path(targetpath, projectname)
    return os.path.join(path, "functionindex.json")


def save_functionindex(
        targetpath: str, projectname: str, d: Dict[str, Any]) -> None:
    filename = get_functionindex_filename(targetpath, projectname)
    with open(filename, "w") as fp:
        json.dump(d, fp)


def load_functionindex(targetpath: str, projectname: str) -> Dict[str, Any]:
    filename = get_functionindex_filename(targetpath, projectname)
    if os.path.isfile(filename):
        with open(filename, "r") as fp:
            return json.load(fp)
    return {}


def get_callgraph_filename(targetpath: str, projectname: str) -> str:
    path = get_analysisresults_path(targetpath, projectname)
    return os.path.join(path, "callgraph.json")


def save_callgraph(targetpath: str, projectname: str, d: Dict[str, Any]) -> None:
    filename = get_callgraph_filename(targetpath, projectname)
    with open(filename, "w") as fp:
        json.dump(d, fp)


def load_callgraph(targetpath: str, projectname: str) -> Dict[str, Any]:
    filename = get_callgraph_filename(targetpath, projectname)
    if os.path.isfile(filename):
        with open(filename, "r") as fp:
            return json.load(fp)
    return {}


def get_preserves_memory_functions_filename(path: str) -> str:
    return os.path.join(path, "preserves-memory.json")


def save_preserves_memory_functions(path: str, d: Dict[str, Any]) -> None:
    filename = get_preserves_memory_functions_filename(path)
    with open(filename, "w") as fp:
        json.dump(d, fp, indent=2)


def load_preserves_memory_functions(path: str) -> Dict[str, Any]:
    filename = get_preserves_memory_functions_filename(path)
    if os.path.isfile(filename):
        with open(filename, "r") as fp:
            return json.load(fp)
    return {}


def get_targetfiles_xnode(
        targetpath: str, projectname: str) -> Optional[ET.Element]:
    filename = get_targetfiles_filename(targetpath, projectname)
    return get_xnode(
        filename, "c-files", "File that holds the names of source files")


def get_targetfiles_list(
        targetpath: str,
        projectname: str) -> List[Tuple[Optional[str], Optional[str]]]:
    result: List[Tuple[Optional[str], Optional[str]]] = []
    node = get_targetfiles_xnode(targetpath, projectname)
    if node is not None:
        for f in node.findall("c-file"):
            result.append((f.get("id"), f.get("name")))
    return result


def get_global_definitions_filename(targetpath: str, projectname: str) -> str:
    path = get_analysisresults_path(targetpath, projectname)
    return os.path.join(path, "globaldefinitions.xml")


def archive_project_summary_results(path: str, projectname: str) -> None:
    if os.path.isdir(path):
        projectsummary = projectname + "_summaryresults"
        filename = os.path.join(path, projectsummary + ".json")
        if os.path.isfile(filename):
            with open(filename) as fp:
                d = json.load(fp)
                if "timestamp" in d:
                    dtime = d["timestamp"]
                else:
                    dtime = 0
                newfilename = projectsummary + "_" + str(dtime) + ".json"
                newfilename = os.path.join(path, newfilename)
                with open(newfilename, "w") as fp:
                    json.dump(d, fp)


def save_project_summary_results(
        path: str, projectname: str, d: Dict[str, Any]) -> None:
    archive_project_summary_results(path, projectname)
    projectsummary = projectname + "_summaryresults"
    with open(os.path.join(path, projectsummary + ".json"), "w") as fp:
        json.dump(d, fp)


def save_project_summary_results_as_xml(
        path: str, projectname: str, d: Dict[str, Any]) -> None:
    projectsummary = projectname + "_summaryresults"
    xml_file = os.path.join(path, projectsummary + ".xml")
    tags = d["tagresults"]
    ppos = tags["ppos"]
    spos = tags["spos"]
    files = d["fileresults"]
    file_ppos = files["ppos"]
    file_spos = files["spos"]

    xml_root = ET.Element("xml-root")
    tagresults = ET.SubElement(xml_root, "tagresults")
    ppos_xml = ET.SubElement(tagresults, "ppos")
    spos_xml = ET.SubElement(tagresults, "spos")
    fileresults = ET.SubElement(xml_root, "fileresults")
    file_ppos_xml = ET.SubElement(fileresults, "ppos")
    file_spos_xml = ET.SubElement(fileresults, "spos")

    for key in ppos:
        ppo_type = ET.SubElement(ppos_xml, "ppo")
        ppo_type.set("name", key)
        for val in ppos[key]:
            stat = ppos[key][val]
            ppo_type.set(val, str(stat))

    for key in spos:
        spo_type = ET.SubElement(spos_xml, "spo")
        spo_type.set("name", key)
        for val in spos[key]:
            stat = spos[key][val]
            spo_type.set(val, str(stat))

    for key in file_ppos:
        ppo_file = ET.SubElement(file_ppos_xml, "ppo")
        ppo_file.set("name", key)
        stats_dict = file_ppos[key]
        for stats in stats_dict:
            ppo_file.set(stats, str(stats_dict[stats]))

    for key in file_spos:
        spo_file = ET.SubElement(file_spos_xml, "spo")
        spo_file.set("name", key)
        stats_dict = file_spos[key]
        for stats in stats_dict:
            spo_file.set(stats, str(stats_dict[stats]))

    tree = ET.ElementTree(xml_root)
    tree.write(xml_file)


def read_project_summary_results(
        path: str,
        projectname: str
) -> Optional[Dict[str, Dict[str, Dict[str, Dict[str, int]]]]]:
    if os.path.isdir(path):
        projectsummary = projectname + "_summaryresults"
        filename = os.path.join(path, projectsummary + ".json")
        if os.path.isfile(filename):
            with open(filename) as fp:
                d = json.load(fp)
                return cast(Dict[str, Dict[str, Dict[str, Dict[str, int]]]], d)
        else:
            print("Warning: " + filename + " not found: summarize results first")
    else:
        print("Warning: " + path + " not found: please check path name")
    return None


def read_project_summary_results_history(
        path: str, projectname: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if os.path.isdir(path):
        for fname in os.listdir(path):
            projectsummary = projectname + "_summaryresults"
            if fname.startswith(projectsummary) and fname.endswith("json"):
                fname = os.path.join(path, fname)
                with open(fname) as fp:
                    result.append(json.load(fp))
    return result


def get_global_declarations_xnode(
        targetpath: str, projectname: str) -> Optional[ET.Element]:
    filename = get_global_definitions_filename(targetpath, projectname)
    return get_xnode(
        filename, "globals", "Global type dictionary file", show=False)


def get_global_dictionary_xnode(
        targetpath: str, projectname: str) -> Optional[ET.Element]:
    filename = get_global_definitions_filename(targetpath, projectname)
    gnode = get_xnode(
        filename, "globals", "Global type declarations file", show=False)
    if gnode is not None:
        return gnode.find("dictionary")
    return None


# ------------------------------------------------------------------- files ----


def get_cfilenamebase(cfilename: str) -> str:
    if cfilename.endswith(".c"):
        return cfilename[:-2]
    return cfilename


def get_cfile_filepath(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    resultspath = get_analysisresults_path(targetpath, projectname)
    if cfilepath is None:
        return os.path.join(resultspath, cfilename)
    else:
        path = os.path.join(resultspath, cfilepath)
        return os.path.join(path, cfilename)


def get_cfile_cfile(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_cfile.xml")


def get_cfile_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_cfile(targetpath, projectname, cfilepath, cfilename)
    return get_xnode(filename, "c-file", "C source file")


def check_cfile_results(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[str]:
    filename = get_cfile_cfile(targetpath, projectname, cfilepath, cfilename)
    if os.path.isfile(filename):
        try:
            get_xnode(filename, "c-file", "C source file")
        except CHCXmlParseError as e:
            return str(e)
        return None
    else:
        return (
            f"Results file {filename} not found found for c-file {cfilename}")


def get_cfile_dictionaryname(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_cdict.xml")


def get_cfile_dictionary_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_dictionaryname(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(filename, "cfile", "C dictionary file")


def get_cfile_predicate_dictionaryname(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_prd.xml")


def get_cfile_predicate_dictionary_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_predicate_dictionaryname(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(
        filename, "po-dictionary", "PO predicate dictionary file", show=False)


def get_cfile_assignment_dictionaryname(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_cgl.xml")


def get_cfile_assignment_dictionary_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_assignment_dictionaryname(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(
        filename,
        "assignment-dictionary",
        "Global assignments dictionary file",
        show=False)


def get_cfile_interface_dictionaryname(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_ixf.xml")


def get_cfile_interface_dictionary_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_interface_dictionaryname(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(
        filename,
        "interface-dictionary",
        "Interface objects dictionary file",
        show=False,
    )


def save_cfile_interface_dictionary(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        xnode: ET.Element) -> None:
    filename = get_cfile_interface_dictionaryname(
        targetpath, projectname, cfilepath, cfilename)
    header = UX.get_xml_header(filename, "interfacedictionary")
    header.append(xnode)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(header)))


def get_cfile_contexttablename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_ctxt.xml")


def get_cfile_contexttable_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cfile_contexttablename(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(filename, "c-contexts", "C contexts file", show=False)


def get_cfile_directory(path: str, cfilename: str) -> str:
    return os.path.join(path, get_cfilenamebase(cfilename))


def get_cfile_logfiles_directory(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, "logfiles")


def get_cfile_logfile_name(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        kind: str) -> str:
    filepath = get_cfile_logfiles_directory(
        targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_" + kind)


def get_cxreffile_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, cfilename + "_gxrefs.xml")


def get_cxreffile_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> Optional[ET.Element]:
    filename = get_cxreffile_filename(
        targetpath, projectname, cfilepath, cfilename)
    return get_xnode(
        filename, "global-xrefs", "File with global cross references", show=False)


def get_global_invs_filename(path: str, cfilename: str, objectname: str) -> str:
    if cfilename.endswith(".c"):
        cfilename = cfilename[:-2]
    objectname = "" if objectname == "all" else "_" + objectname
    return os.path.join(path, cfilename + objectname + "_ginvs.xml")


def get_cfile_usr_filename(path: str, cfilename: str) -> str:
    return os.path.join(path, (get_cfilenamebase(cfilename) + "_usr.xml"))


# ----------------------------------------------------------------- functions --


def get_cfun_basename(path: str, cfilename: str, fname: str) -> str:
    cfilename = get_cfilenamebase(cfilename)
    cfiledir = os.path.join(path, cfilename)
    basename = os.path.basename(cfilename)
    return os.path.join(cfiledir, basename + "_" + fname)


def get_fn_composite(cfilename: str, fnname: str, ext: str) -> str:
    return "_".join([cfilename, fnname, ext]) + ".xml"


def get_cfile_fnspath(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str) -> str:
    filepath = get_cfile_filepath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(filepath, "functions")


def get_cfile_fnpath(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnspath = get_cfile_fnspath(targetpath, projectname, cfilepath, cfilename)
    return os.path.join(fnspath, fnname)


def get_cfun_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "cfun")
    return os.path.join(fnpath, filename)


def get_cfun_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_cfun_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(filename, "function", "C source function file")


def check_cfun_results(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[str]:
    r = check_cfile_results(targetpath, projectname, cfilepath, cfilename)
    if r is not None:
        return r

    filename = get_cfun_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    if os.path.isfile(filename):
        try:
            get_xnode(filename, "function", "C source function file")
        except CHCXmlParseError as e:
            return str(e)
        return None
    else:
        return (f"Results file {filename} not found for function {fnname}")


def get_api_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "api")
    return os.path.join(fnpath, filename)


def get_api_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_api_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(filename, "function", "Function api file", show=False)


def save_api(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str,
        xnode: ET.Element) -> None:
    filename = get_api_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    header = UX.get_xml_header(filename, "api")
    header.append(xnode)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(header)))


def get_vars_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "vars")
    return os.path.join(fnpath, filename)


def get_vars_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_vars_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(
        filename, "function", "Function variable dictionary", show=False)


def get_invs_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "invs")
    return os.path.join(fnpath, filename)


def get_invs_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_invs_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(filename, "function", "Function invariants", show=False)


def get_pod_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "pod")
    return os.path.join(fnpath, filename)


def get_pod_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_pod_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(
        filename, "function", "Function proof obligation types", show=False
    )


def get_ppo_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "ppo")
    return os.path.join(fnpath, filename)


def get_ppo_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_ppo_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(filename, "function", "Primary proof obligations file")


def get_spo_filename(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> str:
    fnpath = get_cfile_fnpath(
        targetpath, projectname, cfilepath, cfilename, fnname)
    filename = get_fn_composite(cfilename, fnname, "spo")
    return os.path.join(fnpath, filename)


def get_spo_xnode(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str) -> Optional[ET.Element]:
    filename = get_spo_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    return get_xnode(
        filename, "function", "Secondary proof obligations file", show=False)


def save_spo_file(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str,
        cnode: ET.Element) -> None:
    filename = get_spo_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    header = UX.get_xml_header(cfilename, "spos")
    header.append(cnode)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(header)))
    chklogger.logger.info("Saved spo file: %s", filename)


def save_pod_file(
        targetpath: str,
        projectname: str,
        cfilepath: Optional[str],
        cfilename: str,
        fnname: str,
        cnode: ET.Element) -> None:
    filename = get_pod_filename(
        targetpath, projectname, cfilepath, cfilename, fnname)
    header = UX.get_xml_header(filename, "pod")
    header.append(cnode)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(header)))
    chklogger.logger.info("Saved pod file: %s", filename)


# --------------------------------------------------------------- source code --


def get_src_filename(path: str, cfilename: str) -> str:
    return os.path.join(path, cfilename)


def get_srcfile_lines(path: str, cfilename: str) -> List[str]:
    filename = get_src_filename(path, cfilename)
    with open(filename, "r") as fp:
        return fp.readlines()


# --------------------------------------------------------------- contracts ----


def has_contracts(path: str, cfilename: str) -> bool:
    filename = os.path.join(path, cfilename + "_c.xml")
    return os.path.isfile(filename)


def has_candidate_contracts(path: str, cfilename: str) -> bool:
    filename = os.path.join(path, cfilename + "_cc.xml")
    return os.path.isfile(filename)


def get_contracts(path: str, cfilename: str) -> Optional[ET.Element]:
    filename = os.path.join(path, cfilename + "_c.xml")
    if os.path.isfile(filename):
        return get_xnode(filename, "cfile", "Contract file", show=True)
    else:
        return None


def get_candidate_contracts(path: str, cfilename: str) -> Optional[ET.Element]:
    filename = os.path.join(path, cfilename + "_cc.xml")
    return get_xnode(filename, "cfile", "Contract file", show=True)


def has_global_contract(path: str) -> bool:
    filename = os.path.join(path, "globaldefs.json")
    return os.path.isfile(filename)


def has_global_xml_contract(path: str) -> bool:
    filename = os.path.join(path, "globaldefs.xml")
    return os.path.isfile(filename)


def get_global_contract(path: str) -> Dict[str, Any]:
    filename = os.path.join(path, "globaldefs.json")
    if os.path.isfile(filename):
        with open(filename, "r") as fp:
            return json.load(fp)
    return {}


def get_global_xml_contract(path: str) -> Optional[ET.Element]:
    filename = os.path.join(path, "globaldefs.xml")
    return get_xnode(filename, "global-definitions", "Global contract file")


def _save_contracts_file_aux(path: str, filename: str, cnode: ET.Element) -> None:
    filedir = os.path.dirname(filename)
    if not os.path.isdir(filedir):
        os.makedirs(filedir)
    root = UX.get_xml_header("cfile", "cfile")
    root.append(cnode)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(root)))


def save_contracts_file(path: str, cfilename: str, cnode: ET.Element) -> None:
    filename = os.path.join(path, cfilename + "_c.xml")
    _save_contracts_file_aux(path, filename, cnode)


def save_global_xml_contract(path: str, cnode: ET.Element) -> None:
    filename = os.path.join(path, "globaldefs.xml")
    root = UX.get_xml_header("codehawk-contract-file", "codehawk-contract-file")
    root.append(cnode)
    if os.path.isfile(filename):
        create_backup_file(filename)
    with open(filename, "w") as fp:
        fp.write(UX.doc_to_pretty(ET.ElementTree(root)))


def save_candidate_contracts_file(path: str, cfilename: str, cnode: ET.Element) -> None:
    filename = os.path.join(path, cfilename + "_cc.xml")
    _save_contracts_file_aux(path, filename, cnode)


# ------------------------------------------------------------ exported --


def save_cx_file(path: str, cfilename: str, d: Dict[str, Any]) -> None:
    cxdir = os.path.join(path, "exportfiles")
    if not os.path.isdir(cxdir):
        os.makedirs(cxdir)
    filename = os.path.join(cxdir, cfilename + "_cx.json")
    filedir = os.path.dirname(filename)
    if not os.path.isdir(filedir):
        os.makedirs(filedir)
    with open(filename, "w") as fp:
        json.dump(d, fp, indent=3)


# ------------------------------------------------------------ projects --


def get_testdata_dict() -> Dict[str, Any]:
    testdatapath = os.path.join(Config().testdir, "testdata")
    testdatafile = os.path.join(testdatapath, "testprojects.json")
    if os.path.isfile(testdatafile):
        with open(testdatafile, "r") as fp:
            testdata = json.load(fp)
            return testdata
    return {}


def get_project_logfilename(path: str) -> str:
    testdir = Config().testdir
    testdata = get_testdata_dict()
    if path in testdata:
        logpath = os.path.join(testdir, str(testdata[path]["path"]))
        logfile = os.path.join(logpath, path + ".chc_analysis_log")
        return logfile
    else:
        logfile = os.path.join(path, "log.chc_analysis_log")
        return logfile


def list_test_applications() -> str:
    testdata = get_testdata_dict()
    lines = []
    lines.append("*" * 80)
    lines.append("Test applications currently provided:")
    lines.append("-" * 80)
    maxlen = max(len(name) for name in testdata) + 5
    for name in sorted(testdata):
        lines.append(name.ljust(maxlen) + testdata[name]["path"])
    lines.append("*" * 80)
    return "\n".join(lines)


# ------------------------------------------------------------ kendra tests ----


def get_kendra_path() -> str:
    return Config().kendradir


def get_kendra_testpath(testname: str) -> str:
    dirname = os.path.join(get_kendra_path(), testname)
    if not os.path.isdir(dirname):
        raise CHCDirectoryNotFoundError(dirname)
    return dirname


def get_kendra_testpath_byid(testid: int) -> str:
    testname = "id" + str(testid) + "Q"
    testpath = get_kendra_testpath(testname)
    if os.path.isdir(testpath):
        return testpath
    raise CHCDirectoryNotFoundError(testpath)


def get_kendra_cpath(cfilename: str) -> str:
    if cfilename.endswith(".c"):
        testid = int(cfilename[2:-2])
        testbase = (((testid - 115) // 4) * 4) + 115
        return get_kendra_testpath_byid(int(testbase))
    else:
        raise CHCFileNotFoundError(cfilename)


# --------------------------------------------------------libc summary tests ---


def get_libc_summary_test_path() -> str:
    return Config().libcsummarytestdir


def get_libc_summary_test_list() -> Dict[str, Any]:
    path = get_libc_summary_test_path()
    filename = os.path.join(path, "testfiles.json")
    try:
        with open(filename, "r") as fp:
            return json.load(fp)["headers"]
    except ValueError as e:
        raise CHCJSONParseError(filename, e)


def get_libc_summary_test(header: str, functionname: str) -> Tuple[str, str]:
    testdir = get_libc_summary_test_path()
    testfiles = get_libc_summary_test_list()
    if header in testfiles:
        summaries = testfiles[header]
        if functionname in summaries["files"]:
            path = os.path.join(testdir, summaries["path"])
            path = os.path.join(path, summaries["files"][functionname]["path"])
            file = summaries["files"][functionname]["file"]
            return (path, file)
        else:
            raise CHCSummaryTestNotFound(
                header, functionname, summaries["files"].keys()
            )
    else:
        raise CHCSummaryHeaderNotFound(header, list(testfiles.keys()))


# ------------------------------------------------------------ juliet tests ----


def get_juliet_path() -> str:
    juliettarget = config.targets.get("juliet", None)
    if juliettarget is None:
        raise CHCJulietTestSuiteNotRegisteredError()
    if not os.path.isfile(juliettarget):
        raise CHCJulietTestSuiteFileNotFoundError(juliettarget)
    return os.path.dirname(juliettarget)


def get_juliet_target_file() -> Dict[str, Any]:
    path = get_juliet_path()
    filename = os.path.join(path, "juliettestcases.json")
    if os.path.isfile(filename):
        with open(filename) as fp:
            try:
                return json.load(fp)
            except ValueError as e:
                raise CHCJSONParseError(filename, e)
    else:
        raise CHCFileNotFoundError(filename)


def get_juliet_testcases() -> Dict[str, Dict[str, List[str]]]:
    juliettargetfile = get_juliet_target_file()
    if "testcases" in juliettargetfile:
        return juliettargetfile["testcases"]
    else:
        raise CHCJulietTargetFileCorruptedError("testcases")


def get_juliet_variant_descriptions() -> Dict[str, Any]:
    juliettargetfile = get_juliet_target_file()
    if "variants" in juliettargetfile:
        return juliettargetfile["variants"]
    else:
        raise CHCJulietTargetFileCorruptedError("variants")


def get_flattened_juliet_testcases() -> Dict[str, List[str]]:
    testcases = get_juliet_testcases()
    result: Dict[str, List[str]] = {}
    for cwe in testcases:
        result[cwe] = []
        for t in testcases[cwe]:
            result[cwe].extend(testcases[cwe][t])
    return result


def get_juliet_summaries() -> str:
    path = get_juliet_path()
    summarypath = os.path.join(path, "testcasesupport")
    summarypath = os.path.join(summarypath, "julietsummaries")
    return os.path.join(summarypath, "julietsummaries.jar")


def get_juliet_testpath(cwe: str, test: str) -> str:
    julietpath = get_juliet_path()
    testcases = get_juliet_testcases()
    if cwe not in testcases:
        raise CHCJulietCWENotFoundError(cwe, list(testcases.keys()))
    cwepath = os.path.join(julietpath, cwe)
    for subset in testcases[cwe]:
        if test in testcases[cwe][subset]:
            if subset == "top":
                return os.path.join(cwepath, test)
            else:
                subpath = os.path.join(cwepath, subset)
                return os.path.join(subpath, test)
    tests: List[Any] = []
    for s in testcases[cwe]:
        tests.extend(testcases[cwe][s])
    raise CHCJulietTestNotFoundError(cwe, test, tests)


def save_juliet_test_summary(cwe: str, test: str, d: Dict[str, Any]) -> None:
    path = get_juliet_testpath(cwe, test)
    with open(os.path.join(path, "jsummaryresults.json"), "w") as fp:
        json.dump(d, fp, sort_keys=True)


def read_juliet_test_summary(cwe: str, test: str) -> Optional[Dict[str, Any]]:
    path = get_juliet_testpath(cwe, test)
    if os.path.isdir(path):
        filename = os.path.join(path, "jsummaryresults.json")
        if os.path.isfile(filename):
            with open(filename) as fp:
                d = json.load(fp)
            return d
    return None


def check_juliet_test_summary(cwe: str, test: str) -> None:
    path = get_juliet_testpath(cwe, test)
    if os.path.isdir(path):
        filename = os.path.join(path, "jsummaryresults.json")
        if os.path.isfile(filename):
            return
    raise CHCJulietScoreFileNotFoundError(cwe, test)


def get_juliet_scorekey(cwe: str, test: str) -> Dict[str, Any]:
    path = get_juliet_testpath(cwe, test)
    scorekey = os.path.join(path, "scorekey.json")
    if os.path.isfile(scorekey):
        with open(scorekey, "r") as fp:
            d = json.load(fp)
        return d
    raise CHCJulietScoreKeyNotFoundError(cwe, test)


def chtime(t: float) -> str:
    if t == 0:
        return "0"
    return time.strftime("%Y-%m-%d %H:%m", time.localtime(t))


def get_juliet_result_times(cwe: str, test: str) -> Tuple[str, str]:
    t1 = 0.0
    t2 = 0.0
    path = get_juliet_testpath(cwe, test)
    projectname = cwe + "_" + test
    cchpath = get_cchpath(path, projectname)
    if os.path.isdir(cchpath):
        analysispath = os.path.join(cchpath, "a")
        if os.path.isdir(analysispath):
            t1 = os.path.getmtime(analysispath)
    resultsfile = os.path.join(path, "jsummaryresults.json")
    if os.path.isfile(resultsfile):
        t2 = os.path.getmtime(resultsfile)
    return (chtime(t1), chtime(t2))


# ----------------------------------------------------------- itc tests  ------


def get_itc_path() -> str:
    sardpath = os.path.join(Config().testdir, "sard")
    return os.path.abspath(os.path.join(sardpath, "itc"))


def get_itc_testpath(testname: str) -> str:
    return os.path.join(get_itc_path(), testname)


# ----------------------------------------------------------- cgc tests  ------


def get_cgc_path() -> str:
    return os.path.join(Config().testdir, "cgc")


def make_cgc_challenge_path(testname: str, targetname: str) -> str:
    challengepath = os.path.join(get_cgc_path(), "challenges")
    testpath = os.path.join(challengepath, testname)
    if not os.path.isdir(testpath):
        os.mkdir(testpath)
    tgtpath = os.path.join(testpath, targetname)
    if not os.path.isdir(tgtpath):
        os.mkdir(tgtpath)
    return tgtpath


def get_cgc_challenge_path(testname: str, targetname: str) -> str:
    challengepath = os.path.join(get_cgc_path(), "challenges")
    testpath = os.path.join(challengepath, testname)
    tgtpath = os.path.join(testpath, targetname)
    return tgtpath


def get_cgc_challenges() -> Dict[str, Any]:
    challenges = os.path.join(get_cgc_path(), "challenges.json")
    with open(challenges, "r") as fp:
        tests = json.load(fp)
        if tests is not None:
            return tests["challenges"]
    return {}


def get_cgc_test_targets(t: str) -> List[Any]:
    challenges = get_cgc_challenges()
    if t in challenges:
        return challenges[t]["targets"]
    else:
        return []


def read_cgc_summary_results(
    testname: str, targetname: str
) -> Optional[Dict[str, Any]]:
    path = get_cgc_challenge_path(testname, targetname)
    if os.path.isdir(path):
        filename = os.path.join(path, "summaryresults.json")
        if os.path.isfile(filename):
            with open(filename) as fp:
                d = json.load(fp)
            return d
    return None


def get_cgc_summaries() -> str:
    cgcpath = get_cgc_path()
    summarypath = os.path.join(cgcpath, "cgcsummaries")
    return os.path.join(summarypath, "cgcsummaries.jar")


# ---------------------------------------------------- functional tests  ------


def get_functional_tests_path() -> str:
    return os.path.join(Config().testdir, "functional")


def get_functional_testpath(testpath: str) -> str:
    return os.path.join(get_functional_tests_path(), testpath)


# ---------------------------------------------------- workshop files  ------


def get_workshop_path() -> str:
    return os.path.join(Config().testdir, "workshop")


def get_workshop_list() -> Optional[Dict[str, Any]]:
    filename = os.path.join(get_workshop_path(), "workshop.json")
    if os.path.isfile(filename):
        with open(filename) as fp:
            workshoplist = json.load(fp)
            return workshoplist
    return None


def get_workshop_file_data(project: str, wfile: str) -> Optional[Dict[str, Any]]:
    wspath = get_workshop_path()
    workshoplist = get_workshop_list()
    if workshoplist is not None:
        if project in workshoplist:
            projectlist = workshoplist[project]
            if wfile in projectlist:
                wsdata = projectlist[wfile]
                filedata: Dict[str, Any] = {}
                filedata["summaries"] = []
                filedata["file"] = wsdata["file"]
                filedata["path"] = os.path.join(wspath, wsdata["path"])
                for s in wsdata["summaries"]:
                    filedata["summaries"].append(os.path.join(wspath, s))
                return filedata
            else:
                print("*" * 80)
                print(
                    "Workshop file: "
                    + wfile
                    + " not foound in project: "
                    + project
                    + "."
                )
                print("Available files are in project: " + project + " are:")
                print("-" * 80)
                for name in projectlist:
                    print(
                        name.ljust(8)
                        + ": "
                        + projectlist[name]["path"]
                        + ", "
                        + projectlist[name]["file"]
                    )
                print("-" * 80)
                exit(0)
        else:
            print("*" * 80)
            print("Workshop project: " + project + " not found.")
            print("Available workshop projects are:")
            print("-" * 80)
            for name in workshoplist:
                print(name)
            print("-" * 80)
            exit(0)
    return None


# ------------------------------------------------------------ unzip tar file --


def unpack_tar_file(path: str, deletesemantics: bool = False) -> bool:
    linuxtargzfile = "semantics_linux.tar.gz"
    mactargzfile = "semantics_mac.tar.gz"
    if not os.path.isdir(path):
        raise CHCDirectoryNotFoundError(path)
    cwd = os.getcwd()
    os.chdir(path)

    if os.path.isfile(linuxtargzfile):
        targzfile = linuxtargzfile
    elif os.path.isfile(mactargzfile):
        targzfile = mactargzfile
    elif os.path.isdir("semantics") and not deletesemantics:
        os.chdir(cwd)
        return True
    else:
        os.chdir(cwd)
        return False

    if os.path.isdir("semantics"):
        if deletesemantics:
            print("Removing existing semantics directory")
            shutil.rmtree("semantics")
        else:
            os.chdir(cwd)
            return True

    if os.path.isfile(targzfile):
        cmd = ["tar", "xfz", targzfile]
        result = subprocess.call(cmd, cwd=path, stderr=subprocess.STDOUT)
        if result != 0:
            print("Error in " + " ".join(cmd))
            os.chdir(cwd)
            return False
        # else:
        # print('Successfully extracted ' + targzfile)
    result = os.path.isdir("semantics")
    os.chdir(cwd)
    return result


def unpack_cchtar_file(
        projectpath: str, projectname: str, deletesemantics: bool = False
) -> bool:
    cchdir = projectname + ".cch"
    targzname = cchdir + ".tar.gz"
    if not os.path.isdir(projectpath):
        raise CHCDirectoryNotFoundError(projectpath)
    chklogger.logger.info("Changing directory to %s", projectpath)
    os.chdir(projectpath)

    if os.path.isdir(cchdir):
        if not deletesemantics:
            return True
        else:
            chklogger.logger.info(
                "Removing existing analysis directory %s", cchdir)
            shutil.rmtree(cchdir)

    if os.path.isfile(targzname):
        cmd = ["tar", "xfz", targzname]
        result = subprocess.call(cmd, cwd=projectpath, stderr=subprocess.STDOUT)
        if result != 0:
            chklogger.logger.error("Command %s failed", " ".join(cmd))
            return False

        chklogger.logger.info("Successfully extracted %s", targzname)
        return True

    else:
        chklogger.logger.error("Semantics tar file %s not found", targzname)
        return False

    return os.path.isdir(cchdir)


def check_semantics(path: str, deletesemantics: bool = False) -> None:
    if unpack_tar_file(path, deletesemantics=deletesemantics):
        return
    raise CHCSemanticsNotFoundError(path)


def check_cch_semantics(
        projectpath: str, projectname: str, deletesemantics: bool = False
) -> None:
    if unpack_cchtar_file(
            projectpath, projectname, deletesemantics=deletesemantics):
        return
    raise CHCSemanticsNotFoundError(projectpath)


def xfind_node(p: ET.Element, tag: str, msg: str) -> ET.Element:
    xnode = p.find(tag)
    if xnode is None:
        raise CHCError(msg + ": element " + tag + " not found")
    else:
        return xnode


def xget_attr(p: ET.Element, tag: str, msg: str) -> str:
    xtag = p.get(tag)
    if xtag is None:
        raise CHCError(msg + ": tag: " + tag + " not found")
    else:
        return xtag


def xget_int_attr(p: ET.Element, tag: str, msg: str) -> int:
    xdata = xget_attr(p, tag, msg)
    return int(xdata)


if __name__ == "__main__":

    config = Config()

    testdir = config.testdir

    print("\nkendra paths:")
    print("-" * 80)
    for id in range(115, 119):
        id_str = "id" + str(id) + ".c"
        try:
            print("  " + id_str + ": " + get_kendra_cpath(str(id)))
        except CHError as e:
            print(str(e.wrap()))
            exit(1)

    print("\nRegistered target files:")
    print("-" * 80)
    targets = get_registered_analysis_targets()
    for group in sorted(targets):
        print(group)
        for project in sorted(targets[group]["projects"]):
            print("  - " + project)
