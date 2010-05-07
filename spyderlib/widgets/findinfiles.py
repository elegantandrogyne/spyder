# -*- coding: utf-8 -*-
#
# Copyright © 2009 Pierre Raybaut
# Licensed under the terms of the MIT License
# (see spyderlib/__init__.py for details)

"""Find in files widget"""

# pylint: disable-msg=C0103
# pylint: disable-msg=R0903
# pylint: disable-msg=R0911
# pylint: disable-msg=R0201

from __future__ import with_statement

try:
    # PyQt4 4.3.3 on Windows (static DLLs) with py2exe installed:
    # -> pythoncom must be imported first, otherwise py2exe's boot_com_servers
    #    will raise an exception ("ImportError: DLL load failed [...]") when
    #    calling any of the QFileDialog static methods (getOpenFileName, ...)
    import pythoncom #@UnusedImport
except ImportError:
    pass

from PyQt4.QtGui import (QHBoxLayout, QWidget, QTreeWidgetItem, QSizePolicy,
                         QRadioButton, QVBoxLayout, QLabel, QFileDialog)
from PyQt4.QtCore import SIGNAL, Qt, QThread, QMutexLocker, QMutex

import sys, os, re, fnmatch
import os.path as osp
from subprocess import Popen, PIPE

# For debugging purpose:
STDOUT = sys.stdout

# Local imports
from spyderlib.utils.programs import is_program_installed
from spyderlib.utils.qthelpers import (get_std_icon, create_toolbutton,
                                       translate, get_filetype_icon)
from spyderlib.config import get_icon
from spyderlib.widgets.comboboxes import PathComboBox, PatternComboBox
from spyderlib.widgets import OneColumnTree


def abspardir(path):
    """Return absolute parent dir"""
    return osp.abspath(osp.join(path, os.pardir))

def get_common_path(pathlist):
    common = osp.commonprefix(pathlist)
    if len(common) > 1:
        if not osp.isdir(common):
            common = osp.dirname(common)
        return osp.abspath(common)

def is_hg_installed():
    return is_program_installed('hg.exe' if os.name == 'nt' else 'hg')

def get_hg_root(path):
    previous_path = path
    while not osp.isdir(osp.join(path, '.hg')):
        path = abspardir(path)
        if path == previous_path:
            return
        else:
            previous_path = path
    return osp.abspath(path)

#def find_files_in_hg_manifest(rootpath, include, exclude):
#    p = Popen("hg manifest", stdout=PIPE)
#    found = []
#    hgroot = get_hg_root(rootpath)
#    for path in p.stdout.read().splitlines():
#        dirname = osp.join('.', osp.dirname(path))
#        if re.search(exclude, dirname+os.sep):
#            continue
#        filename = osp.join('.', osp.dirname(path))
#        if re.search(exclude, filename):
#            continue
#        if re.search(include, filename):
#            found.append(osp.join(hgroot, path))
#    return found
#
#def find_files_in_path(rootpath, include, exclude):
#    found = []
#    for path, dirs, files in os.walk(rootpath):
#        for d in dirs[:]:
#            dirname = os.path.join(path, d)
#            if re.search(exclude, dirname+os.sep):
#                dirs.remove(d)
#        for f in files:
#            filename = os.path.join(path, f)
#            if re.search(exclude, filename):
#                continue
#            if re.search(include, filename):
#                found.append(filename)
#    return found


#def find_string_in_files(texts, filenames, regexp=False):
#    results = {}
#    nb = 0
#    for fname in filenames:
#        for lineno, line in enumerate(file(fname)):
#            for text in texts:
#                if regexp:
#                    found = re.search(text, line)
#                    if found is not None:
#                        break
#                else:
#                    found = line.find(text)
#                    if found > -1:
#                        break
#            if regexp:
#                for match in re.finditer(text, line):
#                    res = results.get(osp.abspath(fname), [])
#                    res.append((lineno+1, match.start(), line))
#                    results[osp.abspath(fname)] = res
#                    nb += 1
#            else:
#                while found > -1:
#                    res = results.get(osp.abspath(fname), [])
#                    res.append((lineno+1, found, line))
#                    results[osp.abspath(fname)] = res
#                    for text in texts:
#                        found = line.find(text, found+1)
#                        if found>-1:
#                            break
#                    nb += 1
#    return results, nb

class SearchThread(QThread):
    def __init__(self, parent):
        QThread.__init__(self, parent)
        self.mutex = QMutex()
        self.stopped = None
        self.results = None
        self.pathlist = None
        self.nb = None
        self.error_flag = None
        self.rootpath = None
        self.python_path = None
        self.hg_manifest = None
        self.include = None
        self.exclude = None
        self.texts = None
        self.text_re = None
        self.completed = None
        self.get_pythonpath_callback = None
        
    def initialize(self, path, python_path, hg_manifest,
                   include, exclude, texts, text_re):
        self.rootpath = path
        self.python_path = python_path
        self.hg_manifest = hg_manifest
        self.include = include
        self.exclude = exclude
        self.texts = texts
        self.text_re = text_re
        self.stopped = False
        self.completed = False
        
    def run(self):
        self.filenames = []
        if self.hg_manifest:
            ok = self.find_files_in_hg_manifest()
        elif self.python_path:
            ok = self.find_files_in_python_path()
        else:
            ok = self.find_files_in_path(self.rootpath)
        if ok:
            self.find_string_in_files()
        self.stop()
        self.emit(SIGNAL("finished(bool)"), self.completed)
        
    def stop(self):
        with QMutexLocker(self.mutex):
            self.stopped = True

    def find_files_in_python_path(self):
        pathlist = os.environ['PYTHONPATH'].split(os.pathsep)
        if self.get_pythonpath_callback is not None:
            pathlist += self.get_pythonpath_callback()
        for path in set(pathlist):
            if osp.isdir(path):
                ok = self.find_files_in_path(path)
                if not ok:
                    break
        return ok

    def find_files_in_hg_manifest(self):
        p = Popen(['hg', 'manifest'], stdout=PIPE,
                  cwd=self.rootpath, shell=True)
        hgroot = get_hg_root(self.rootpath)
        self.pathlist = [hgroot]
        for path in p.stdout.read().splitlines():
            with QMutexLocker(self.mutex):
                if self.stopped:
                    return False
            dirname = osp.dirname(path)
            if re.search(self.exclude, dirname+os.sep):
                continue
            filename = osp.basename(path)
            if re.search(self.exclude, filename):
                continue
            if re.search(self.include, filename):
                self.filenames.append(osp.join(hgroot, path))
        return True
    
    def find_files_in_path(self, path):
        if self.pathlist is None:
            self.pathlist = []
        self.pathlist.append(path)
        for path, dirs, files in os.walk(path):
            with QMutexLocker(self.mutex):
                if self.stopped:
                    return False
            for d in dirs[:]:
                dirname = os.path.join(path, d)
                if re.search(self.exclude, dirname+os.sep):
                    dirs.remove(d)
            for f in files:
                filename = os.path.join(path, f)
                if re.search(self.exclude, filename):
                    continue
                if re.search(self.include, filename):
                    self.filenames.append(filename)
        return True
        
    def find_string_in_files(self):
        self.results = {}
        self.nb = 0
        self.error_flag = False
        for fname in self.filenames:
            with QMutexLocker(self.mutex):
                if self.stopped:
                    return
            try:
                for lineno, line in enumerate(file(fname)):
                    for text in self.texts:
                        if self.text_re:
                            found = re.search(text, line)
                            if found is not None:
                                break
                        else:
                            found = line.find(text)
                            if found > -1:
                                break
                    if self.text_re:
                        for match in re.finditer(text, line):
                            res = self.results.get(osp.abspath(fname), [])
                            res.append((lineno+1, match.start(), line))
                            self.results[osp.abspath(fname)] = res
                            self.nb += 1
                    else:
                        while found > -1:
                            res = self.results.get(osp.abspath(fname), [])
                            res.append((lineno+1, found, line))
                            self.results[osp.abspath(fname)] = res
                            for text in self.texts:
                                found = line.find(text, found+1)
                                if found>-1:
                                    break
                            self.nb += 1
            except IOError, (_errno, _strerror):
                self.error_flag = translate("FindInFiles",
                                  "permission denied errors were encountered")
            except re.error:
                self.error_flag = translate("FindInFiles",
                                  "invalid regular expression")
        self.completed = True
    
    def get_results(self):
        return self.results, self.pathlist, self.nb, self.error_flag


class FindOptions(QWidget):
    """
    Find widget with options
    """
    def __init__(self, parent, search_text, search_text_regexp, search_path,
                 include, include_regexp, exclude, exclude_regexp,
                 supported_encodings):
        QWidget.__init__(self, parent)
        
        if search_path is None:
            search_path = os.getcwdu()
        
        if not isinstance(search_text, (list, tuple)):
            search_text = [search_text]
        if not isinstance(search_path, (list, tuple)):
            search_path = [search_path]
        if not isinstance(include, (list, tuple)):
            include = [include]
        if not isinstance(exclude, (list, tuple)):
            exclude = [exclude]

        self.supported_encodings = supported_encodings

        # Layout 1
        hlayout1 = QHBoxLayout()
        self.search_text = PatternComboBox(self, search_text,
                                    translate('FindInFiles', "Search pattern"))
        self.edit_regexp = create_toolbutton(self, get_icon("advanced.png"),
                         tip=translate('FindInFiles', "Regular expression"))
        self.edit_regexp.setCheckable(True)
        self.edit_regexp.setChecked(search_text_regexp)
        self.ok_button = create_toolbutton(self,
                                text=translate('FindInFiles', "Search"),
                                triggered=lambda: self.emit(SIGNAL('find()')),
                                icon=get_std_icon("DialogApplyButton"),
                                tip=translate('FindInFiles', "Start search"))
        self.connect(self.ok_button, SIGNAL('clicked()'), self.update_combos)
        self.stop_button = create_toolbutton(self,
                                text=translate('FindInFiles', "Stop"),
                                triggered=lambda: self.emit(SIGNAL('stop()')),
                                icon=get_icon("terminate.png"),
                                tip=translate('FindInFiles', "Stop search"))
        self.stop_button.setEnabled(False)
        for widget in [self.search_text, self.edit_regexp,
                       self.ok_button, self.stop_button]:
            hlayout1.addWidget(widget)

        # Layout 2
        hlayout2 = QHBoxLayout()
        self.include_pattern = PatternComboBox(self, include,
                        translate('FindInFiles', "Included filenames pattern"))
        self.include_regexp = create_toolbutton(self, get_icon("advanced.png"),
                                            tip=translate('FindInFiles',
                                                          "Regular expression"))
        self.include_regexp.setCheckable(True)
        self.include_regexp.setChecked(include_regexp)
        include_label = QLabel(translate('FindInFiles', "Include:"))
        include_label.setBuddy(self.include_pattern)
        self.exclude_pattern = PatternComboBox(self, exclude,
                        translate('FindInFiles', "Excluded filenames pattern"))
        self.exclude_regexp = create_toolbutton(self, get_icon("advanced.png"),
                                            tip=translate('FindInFiles',
                                                          "Regular expression"))
        self.exclude_regexp.setCheckable(True)
        self.exclude_regexp.setChecked(exclude_regexp)
        exclude_label = QLabel(translate('FindInFiles', "Exclude:"))
        exclude_label.setBuddy(self.exclude_pattern)
        for widget in [include_label, self.include_pattern,
                       self.include_regexp,
                       exclude_label, self.exclude_pattern,
                       self.exclude_regexp]:
            hlayout2.addWidget(widget)

        # Layout 3
        hlayout3 = QHBoxLayout()
        self.python_path = QRadioButton(translate('FindInFiles',
                                        "PYTHONPATH"), self)
        self.python_path.setToolTip(translate('FindInFiles',
                          "Search in all directories listed in sys.path which"
                          " are outside the Python installation directory"))        
        self.hg_manifest = QRadioButton(translate('FindInFiles',
                                                  "Hg repository"), self)
        self.detect_hg_repository()
        self.hg_manifest.setToolTip(translate('FindInFiles',
                              "Search in current directory hg repository"))
        self.custom_dir = QRadioButton(translate('FindInFiles', "Here:"), self)
        self.custom_dir.setChecked(True)
        self.dir_combo = PathComboBox(self)
        self.dir_combo.addItems(search_path)
        self.dir_combo.setToolTip(translate('FindInFiles',
                                    "Search recursively in this directory"))
        self.connect(self.dir_combo, SIGNAL("open_dir(QString)"),
                     self.set_directory)
        self.connect(self.python_path, SIGNAL('toggled(bool)'),
                     self.dir_combo.setDisabled)
        self.connect(self.hg_manifest, SIGNAL('toggled(bool)'),
                     self.dir_combo.setDisabled)
        browse = create_toolbutton(self, get_std_icon('DirOpenIcon'),
                                   tip=translate('FindInFiles',
                                                 'Browse a search directory'),
                                   triggered=self.select_directory)
        for widget in [self.python_path, self.hg_manifest, self.custom_dir,
                       self.dir_combo, browse]:
            hlayout3.addWidget(widget)
            
        self.connect(self.search_text, SIGNAL("valid(bool)"),
                     lambda valid: self.emit(SIGNAL('find()')))
        self.connect(self.include_pattern, SIGNAL("valid(bool)"),
                     lambda valid: self.emit(SIGNAL('find()')))
        self.connect(self.exclude_pattern, SIGNAL("valid(bool)"),
                     lambda valid: self.emit(SIGNAL('find()')))
        self.connect(self.dir_combo, SIGNAL("valid(bool)"),
                     lambda valid: self.emit(SIGNAL('find()')))
            
        vlayout = QVBoxLayout()
        vlayout.addLayout(hlayout1)
        vlayout.addLayout(hlayout2)
        vlayout.addLayout(hlayout3)
        self.setLayout(vlayout)
                
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        
    def update_combos(self):
        self.search_text.lineEdit().emit(SIGNAL('returnPressed()'))
        self.include_pattern.lineEdit().emit(SIGNAL('returnPressed()'))
        self.exclude_pattern.lineEdit().emit(SIGNAL('returnPressed()'))
        
    def detect_hg_repository(self, path=None):
        if path is None:
            path = os.getcwdu()
        hg_repository = is_hg_installed() and get_hg_root(path) is not None
        self.hg_manifest.setEnabled(hg_repository)
        if not hg_repository and self.hg_manifest.isChecked():
            self.custom_dir.setChecked(True)
        
    def set_search_text(self, text):
        self.search_text.setEditText(text)
        self.search_text.lineEdit().selectAll()
        self.search_text.setFocus()
        
    def get_options(self, all=False):
        # Getting options
        utext = unicode(self.search_text.currentText())
        if not utext:
            return
        try:
            texts = [str(utext)]
        except UnicodeEncodeError:
            texts = []
            for encoding in self.supported_encodings:
                try:
                    texts.append( utext.encode(encoding) )
                except UnicodeDecodeError:
                    pass
        text_re = self.edit_regexp.isChecked()
        include = unicode(self.include_pattern.currentText())
        include_re = self.include_regexp.isChecked()
        exclude = unicode(self.exclude_pattern.currentText())
        exclude_re = self.exclude_regexp.isChecked()
        python_path = self.python_path.isChecked()
        hg_manifest = self.hg_manifest.isChecked()
        path = osp.abspath( unicode( self.dir_combo.currentText() ) )
        
        # Finding text occurences
        if not include_re:
            include = fnmatch.translate(include)
        if not exclude_re:
            exclude = fnmatch.translate(exclude)
            
        if all:
            search_text = [unicode(self.search_text.itemText(index)) \
                           for index in range(self.search_text.count())]
            search_path = [unicode(self.dir_combo.itemText(index)) \
                           for index in range(self.dir_combo.count())]
            include = [unicode(self.include_pattern.itemText(index)) \
                       for index in range(self.include_pattern.count())]
            exclude = [unicode(self.exclude_pattern.itemText(index)) \
                       for index in range(self.exclude_pattern.count())]
            return (search_text, text_re, search_path,
                    include, include_re,
                    exclude, exclude_re)
        else:
            return (path, python_path, hg_manifest,
                    include, exclude, texts, text_re)
        
    def select_directory(self):
        """Select directory"""
        self.parent().emit(SIGNAL('redirect_stdio(bool)'), False)
        directory = QFileDialog.getExistingDirectory(self,
                                translate('FindInFiles', "Select directory"),
                                self.dir_combo.currentText())
        if not directory.isEmpty():
            self.set_directory(directory)
        self.parent().emit(SIGNAL('redirect_stdio(bool)'), True)
        
    def set_directory(self, directory):
        path = unicode(osp.abspath(unicode(directory)))
        self.dir_combo.setEditText(path)
        self.detect_hg_repository(path)
        
    def keyPressEvent(self, event):
        """Reimplemented to handle key events"""
        ctrl = event.modifiers() & Qt.ControlModifier
        shift = event.modifiers() & Qt.ShiftModifier
        if event.key() in (Qt.Key_Enter, Qt.Key_Return):
            self.emit(SIGNAL('find()'))
        elif event.key() == Qt.Key_F and ctrl and shift:
            # Toggle find widgets
            self.parent().emit(SIGNAL('toggle_visibility(bool)'),
                               not self.isVisible())
            event.accept()
        else:
            event.ignore()


class ResultsBrowser(OneColumnTree):
    def __init__(self, parent):
        OneColumnTree.__init__(self, parent)
        self.results = None
        self.nb = None
        self.error_flag = None
        self.completed = None
        self.data = None
        self.set_title('')
        self.root_items = None
        
    def activated(self):
        itemdata = self.data.get(self.currentItem())
        if itemdata is not None:
            filename, lineno = itemdata
            self.parent().emit(SIGNAL("edit_goto(QString,int,bool)"),
                               filename, lineno, True)
        
    def set_results(self, search_text, results, pathlist, nb,
                    error_flag, completed):
        self.search_text = search_text
        self.results = results
        self.pathlist = pathlist
        self.nb = nb
        self.error_flag = error_flag
        self.completed = completed
        self.refresh()
        if not self.error_flag and self.nb:
            self.restore()
        
    def refresh(self):
        """
        Refreshing search results panel
        """
        title = "'%s' - " % self.search_text
        if self.results is None:
            text = translate('FindInFiles', 'Search canceled')
        else:
            nb_files = len(self.results)
            if nb_files == 0:
                text = translate('FindInFiles', 'String not found')
            else:
                text_matches = translate('FindInFiles', 'matches in')
                text_files = translate('FindInFiles', 'file')
                if nb_files > 1:
                    text_files += 's'
                text = "%d %s %d %s" % (self.nb, text_matches,
                                        nb_files, text_files)
            if self.error_flag:
                text += ' (' + self.error_flag + ')'
            if not self.completed:
                text += ' (' + translate('FindInFiles', 'interrupted') + ')'
        self.set_title(title+text)
        self.clear()
        self.data = {}
        
        if not self.results: # First search interrupted *or* No result
            return

        # Directory set
        dir_set = set()
        for filename in sorted(self.results.keys()):
            dirname = osp.abspath(osp.dirname(filename))
            dir_set.add(dirname)
                
        # Root path
        root_path_list = None
        _common = get_common_path(list(dir_set))
        if _common is not None:
            root_path_list = [_common]
        else:
            _common = get_common_path(self.pathlist)
            if _common is not None:
                root_path_list = [_common]
            else:
                root_path_list = self.pathlist
        if not root_path_list:
            return
        for _root_path in root_path_list:
            dir_set.add(_root_path)
        # Populating tree: directories
        def create_dir_item(dirname, parent):
            if dirname not in root_path_list:
                displayed_name = osp.basename(dirname)
            else:
                displayed_name = dirname
            item = QTreeWidgetItem(parent, [displayed_name])
            item.setIcon(0, get_std_icon('DirClosedIcon'))
            return item
        dirs = {}
        for dirname in sorted(list(dir_set)):
            if dirname in root_path_list:
                parent = self
            else:
                parent_dirname = abspardir(dirname)
                parent = dirs.get(parent_dirname)
                if parent is None:
                    # This is related to directories which contain found
                    # results only in some of their children directories
                    if osp.commonprefix([dirname]+root_path_list):
                        # create new root path
                        pass
                    items_to_create = []
                    while dirs.get(parent_dirname) is None:
                        items_to_create.append(parent_dirname)
                        parent_dirname = abspardir(parent_dirname)
                    items_to_create.reverse()
                    for item_dir in items_to_create:
                        item_parent = dirs[abspardir(item_dir)]
                        dirs[item_dir] = create_dir_item(item_dir, item_parent)
                    parent_dirname = abspardir(dirname)
                    parent = dirs[parent_dirname]
            dirs[dirname] = create_dir_item(dirname, parent)
        self.root_items = [dirs[_root_path] for _root_path in root_path_list]
        # Populating tree: files
        for filename in sorted(self.results.keys()):
            parent_item = dirs[osp.dirname(filename)]
            file_item = QTreeWidgetItem(parent_item, [osp.basename(filename)])
            file_item.setIcon(0, get_filetype_icon(filename))
            for lineno, colno, line in self.results[filename]:
                item = QTreeWidgetItem(file_item,
                           ["%d (%d): %s" % (lineno, colno, line.rstrip())])
                item.setIcon(0, get_icon('arrow.png'))
                self.data[item] = (filename, lineno)
        # Removing empty directories
        top_level_items = [self.topLevelItem(index)
                           for index in range(self.topLevelItemCount())]
        for item in top_level_items:
            if not item.childCount():
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))


class FindInFilesWidget(QWidget):
    """
    Find in files widget
    """
    def __init__(self, parent,
                 search_text = r"# ?TODO|# ?FIXME|# ?XXX",
                 search_text_regexp=True, search_path=None,
                 include=".", include_regexp=True,
                 exclude=r"\.pyc$|\.orig$|\.hg|\.svn", exclude_regexp=True,
                 supported_encodings=("utf-8", "iso-8859-1", "cp1252")):
        QWidget.__init__(self, parent)
        
        self.setWindowTitle(translate('FindInFiles', 'Find in files'))

        self.search_thread = SearchThread(self)
        self.connect(self.search_thread, SIGNAL("finished(bool)"),
                     self.search_complete)
        
        self.find_options = FindOptions(self, search_text, search_text_regexp,
                                        search_path, include, include_regexp,
                                        exclude, exclude_regexp,
                                        supported_encodings)
        self.connect(self.find_options, SIGNAL('find()'), self.find)
        self.connect(self.find_options, SIGNAL('stop()'), self.stop)
        
        self.result_browser = ResultsBrowser(self)
        
        collapse_btn = create_toolbutton(self, text_beside_icon=False)
        collapse_btn.setDefaultAction(self.result_browser.collapse_all_action)
        expand_btn = create_toolbutton(self, text_beside_icon=False)
        expand_btn.setDefaultAction(self.result_browser.expand_all_action)
        restore_btn = create_toolbutton(self, text_beside_icon=False)
        restore_btn.setDefaultAction(self.result_browser.restore_action)
#        collapse_sel_btn = create_toolbutton(self, text_beside_icon=False)
#        collapse_sel_btn.setDefaultAction(
#                                self.result_browser.collapse_selection_action)
#        expand_sel_btn = create_toolbutton(self, text_beside_icon=False)
#        expand_sel_btn.setDefaultAction(
#                                self.result_browser.expand_selection_action)
        
        btn_layout = QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignTop)
        for widget in [collapse_btn, expand_btn, restore_btn]:
#                       collapse_sel_btn, expand_sel_btn]:
            btn_layout.addWidget(widget)
        
        hlayout = QHBoxLayout()
        hlayout.addWidget(self.result_browser)
        hlayout.addLayout(btn_layout)
        
        layout = QVBoxLayout()
        left, _, right, bottom = layout.getContentsMargins()
        layout.setContentsMargins(left, 0, right, bottom)
        layout.addWidget(self.find_options)
        layout.addLayout(hlayout)
        self.setLayout(layout)
            
    def set_search_text(self, text):
        self.find_options.set_search_text(text)
        
    def set_pythonpath_callback(self, callback):
        self.search_thread.get_pythonpath_callback = callback

    def find(self):
        """Call the find function"""
        options = self.find_options.get_options()
        if options is None:
            return
        self.search_thread.initialize(*options)
        self.search_thread.start()
        self.find_options.ok_button.setEnabled(False)
        self.find_options.stop_button.setEnabled(True)
            
    def stop(self):
        if self.search_thread.isRunning():
            self.search_thread.stop()
            
    def search_complete(self, completed):
        self.find_options.ok_button.setEnabled(True)
        self.find_options.stop_button.setEnabled(False)
        found = self.search_thread.get_results()
        if found is not None:
            results, pathlist, nb, error_flag = found
            search_text = unicode( self.find_options.search_text.currentText() )
            self.result_browser.set_results(search_text, results, pathlist,
                                            nb, error_flag, completed)
            self.result_browser.show()
            
            
def test():
    """Run Find in Files widget test"""
    from spyderlib.utils.qthelpers import qapplication
    app = qapplication()
    widget = FindInFilesWidget(None)
    widget.show()
    sys.exit(app.exec_())
    
if __name__ == '__main__':
    test()
    