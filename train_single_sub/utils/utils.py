import os
import pickle


def load_pkl_data(file_path):
    try:
        with open(file_path, "rb") as f:
            raw_data = pickle.load(f)
        return raw_data
    except Exception as err:
        print(file_path)
        raise err


def join_path(*sub_path):
    path = ""
    for p in sub_path:
        path = os.path.join(path, p)
    if path.startswith("../"):
        path = path.replace("\\", "/")
    return path.replace("\r", "\\r").replace("\n", "\\n")
