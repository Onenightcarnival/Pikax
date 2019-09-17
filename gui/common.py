import multiprocessing as mp
import os
import pickle
import sys
import threading
import tkinter as tk
from tkinter import END, NORMAL, DISABLED, Text, Entry, TclError

import settings

_screen_lock = threading.Lock()


def go_to_next_screen(src, dest):
    global _screen_lock
    if _screen_lock.locked():
        return
    with _screen_lock:
        pikax_handler = src.pikax_handler
        master = src.frame.master
        dest(master, pikax_handler)
        src.destroy()  # destroy after creation to prevent black screen in the middle


def refresh(cls_self):
    global _screen_lock
    if _screen_lock.locked():
        return
    with _screen_lock:
        exec("""
from menu import MenuScreen
from login import LoginScreen
from search import SearchScreen
from rank import RankScreen
from artist import ArtistScreen
from illustration import IllustrationScreen
        """)
        exec(cls_self.__class__.__name__ + '(cls_self.master, cls_self.pikax_handler)')  # create new window
        cls_self.destroy()  # destroy old window


def clear_widget(widget):
    if isinstance(widget, tk.Entry):
        widget.delete(0, tk.END)
    else:  # must be tk.Text
        widget.delete(0.0, tk.END)


def clear_widgets(widgets):
    for widget in widgets:
        clear_widget(widget)


def download(target, args=(), kwargs=()):
    from download import DownloadWindow
    import texts
    kwargs['lang'] = texts.LANG  # manually remember language
    mp.Process(target=DownloadWindow, args=(target, args, kwargs)).start()


def remove_invalid_chars(string):
    return ''.join([s if ord(s) < 65565 else '#' for s in str(string)])


class StdoutTextWidgetRedirector:
    def __init__(self, text_component):
        self.queue = mp.Queue()
        self.text_component = text_component
        self.text_component.tag_configure('center', justify=tk.CENTER)
        threading.Thread(target=self.receiver, daemon=True).start()

    def receiver(self):
        try:
            while True:
                item = self.queue.get()
                self._write(*item)
        except (EOFError, BrokenPipeError) as e:
            sys.stderr.write(str(e))

    def _write(self, string, append=False):
        try:
            string = remove_invalid_chars(string)
            self.text_component.configure(state=NORMAL)

            if isinstance(self.text_component, Text):
                if append:
                    self.text_component.insert(END, '\n' + string)
                else:
                    self.text_component.delete(1.0, END)
                    self.text_component.insert(1.0, string, 'center')
                self.text_component.see(END)
            elif isinstance(self.text_component, Entry):
                self.text_component.delete(0, END)
                self.text_component.insert(0, string)
            else:
                raise TypeError('Not text or entry')
            self.text_component.configure(state=DISABLED)
        except TclError as e:
            sys.stderr.write(str(e))

    def write(self, string, append=False):
        self.queue.put((string, append))

    def flush(self):
        pass


class StdoutPipeWriter:

    def __init__(self, pipe):
        self.pipe = pipe

    def write(self, string):
        self.pipe.put(string)

    def flush(self):
        pass


class StdoutCanvasTextRedirector:
    def __init__(self, canvas, text_id):
        self.text_id = text_id
        self.canvas = canvas
        self.queue = mp.Queue()
        threading.Thread(target=self.receiver, daemon=True).start()

    def receiver(self):
        while True:
            string = self.queue.get()
            self._write(string)

    def write(self, string):
        self.queue.put(string)

    def _write(self, string):
        try:
            self.canvas.itemconfigure(self.text_id, text=remove_invalid_chars(string))
        except TclError as e:
            self.canvas.itemconfigure(self.text_id, text=remove_invalid_chars(str(e)))

    def flush(self):
        pass


def crop_to_dimension(im, width_ratio, height_ratio, focus=tk.CENTER):
    transformed_width = im.height / height_ratio * width_ratio
    if transformed_width < im.width:
        width = transformed_width
        height = im.height
    else:
        height = im.width / width_ratio * height_ratio
        width = im.width

    mid = list(x / 2 for x in im.size)
    half_width = width / 2
    half_height = height / 2

    if focus == tk.CENTER:
        mid = mid
    elif focus == tk.N:
        mid[1] = half_height
    elif focus == tk.S:
        mid[1] = im.size[1] - half_height
    elif focus == tk.W:
        mid[0] = half_width
    elif focus == tk.E:
        mid[0] = im.size[0] - half_width
    else:
        raise ValueError(f'Invalid focus: {focus}')

    left = mid[0] - half_width
    upper = mid[1] - half_height
    right = mid[0] + half_width
    lower = mid[1] + half_height

    return im.crop((left, upper, right, lower))


def get_background_file_path():
    import os
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath('.')

    return os.path.join(base_path, settings.CANVAS_BACKGROUND_PATH)


# https://stackoverflow.com/questions/3352918/how-to-center-a-window-on-the-screen-in-tkinter
def center(win):
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    x = (win.winfo_screenwidth() // 2) - (width // 2)
    y = (win.winfo_screenheight() // 2) - (height // 2)
    win.geometry('{}x{}+{}+{}'.format(width, height, x, y))


def config_root(root, title, width, height):
    root.geometry('{}x{}'.format(width, height))
    root.configure(borderwidth=0, highlightthickness=0)
    root.title(title)
    root.resizable(False, False)
    center(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)


def save_to_local(file_path, item):
    with open(file_path, 'wb') as file:
        pickle.dump(item, file, pickle.HIGHEST_PROTOCOL)


def load_from_local(file_path):
    with open(file_path, 'rb') as file:
        return pickle.load(file)


def remove_local_file(file_path):
    os.remove(file_path)


#
# multiprocessing stuff below
#
def _get_num_of_processes():
    num = os.cpu_count()
    try:
        if settings.MAX_PROCESSES and settings.MAX_PROCESSES > num:
            return settings.MAX_PROCESSES
        else:
            return num
    except AttributeError:
        return num


# total must be positive
def _get_num_of_items_for_each_routine(total, num_of_routine):
    if num_of_routine < 1:
        return 1, total

    num_of_item_for_each_process = int(total / num_of_routine) + 1 if num_of_routine > 1 else int(
        total / num_of_routine)

    if num_of_item_for_each_process < 1:
        num_of_item_for_each_process = 1

    try:
        if settings.MIN_ITEMS_EACH_PROCESS and settings.MIN_ITEMS_EACH_PROCESS > num_of_item_for_each_process:
            return _get_num_of_items_for_each_routine(total, num_of_routine=num_of_routine - 1)
        else:
            return num_of_routine, num_of_item_for_each_process
    except AttributeError:
        return num_of_routine, num_of_item_for_each_process


# basically a copy of StdoutTextRedirector
# rebuild in the new process from the old queue
# to avoid pickling tk app as it is not possible
class QueueWriter:
    def __init__(self, queue):
        self.queue = queue

    def write(self, string, append=False):
        self.queue.put((string, append))

    def flush(self):
        pass


def queue_downloader(target, queue, stdout_queue):
    import sys
    sys.stdout = QueueWriter(stdout_queue)
    try:
        while True:
            item = queue.get()
            if item is None:
                break
            target(item)
    except (EOFError, BrokenPipeError, OSError) as e:
        sys.stderr.write(str(e))


def concurrent_download(target, items):
    num_of_processes = _get_num_of_processes()

    queue = mp.Queue(maxsize=num_of_processes)
    processes = []
    stdout_queue = sys.stdout.queue
    for i in range(num_of_processes):
        process = mp.Process(target=queue_downloader,
                             kwargs={
                                 'target': target,
                                 'queue': queue,
                                 'stdout_queue': stdout_queue
                             },
                             daemon=True
                             )
        processes.append(process)

    for process in processes:
        process.start()

    for item in items:
        queue.put(item)

    for _ in range(num_of_processes):  # tell processes to stop
        queue.put(None)

    for process in processes:
        process.join()


if __name__ == '__main__':
    print(_get_num_of_items_for_each_routine(11, num_of_routine=_get_num_of_processes()))
