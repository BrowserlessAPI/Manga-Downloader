def read_config(name):
    base = "/etc/app/"
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(os.path.realpath(base)):
        raise ValueError("Invalid config name: path traversal detected")
    return open(path).read()
