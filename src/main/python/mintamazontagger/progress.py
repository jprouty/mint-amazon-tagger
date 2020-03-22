from threading import Thread
import time

from progress.bar import IncrementalBar
from progress.counter import Counter
from progress.spinner import Spinner


class AsyncProgress:
    def __init__(self, progress):
        super()
        self.progress = progress
        self.spinning = True
        self.timer = Thread(target=self.runnable)
        self.timer.start()

    def next(self, i=1):
        pass

    def runnable(self):
        while self.spinning:
            self.progress.next()
            time.sleep(0.1)

    def finish(self):
        self.spinning = False
        self.progress.finish()
        print()


class NoProgress:
    def next(self, i=1):
        pass

    def finish(self):
        pass


class QtProgress:
    def __init__(self, msg, max, emitter):
        self.msg = msg
        self.curr = 0
        self.max = max
        self.emitter = emitter

        self.emitter(self.msg, self.max, self.curr)

    def next(self, incr=1):
        self.curr += incr
        if self.curr > self.max:
            self.max += self.max if self.max else 32
        self.emitter(self.msg, self.max, self.curr)

    def finish(self):
        pass


def no_progress_factory(msg, max):
    return NoProgress()


def indeterminate_progress_cli(msg, max=0):
    return AsyncProgress(Spinner(msg))


def determinate_progress_cli(msg, max):
    return IncrementalBar(msg, max=max)


def counter_progress_cli(msg, max=0):
    return Counter(msg + ' - ')
