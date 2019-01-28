from threading import Thread
import time


class AsyncProgress:
    def __init__(self, progress):
        super()
        self.progress = progress
        self.spinning = True
        self.timer = Thread(target=self.runnable)
        self.timer.start()

    def runnable(self):
        while self.spinning:
            self.progress.next()
            time.sleep(0.1)

    def finish(self):
        self.spinning = False
        self.progress.finish()
        print()
