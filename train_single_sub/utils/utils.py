import os
import pickle


def load_pkl_data(file_path):
    try:
        f = open(file_path, 'rb')
        raw_data = pickle.load(f)
        f.close()
        return raw_data
    except Exception as err:
        print(file_path)
        raise err


def join_path(*sub_path):
    path = ''
    for p in sub_path:
        path = os.path.join(path, p)
    if (path.startswith('../')):
        path = path.replace('\\', '/')
    return path.replace('\r', '\\r').replace('\n', '\\n')
