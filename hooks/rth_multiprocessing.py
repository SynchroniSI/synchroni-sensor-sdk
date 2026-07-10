# PyInstaller runtime hook: ensure multiprocessing.freeze_support() is called
# in both the main process and any spawned worker processes.
import multiprocessing

multiprocessing.freeze_support()
