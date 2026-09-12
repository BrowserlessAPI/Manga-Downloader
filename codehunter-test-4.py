   def read_config(name):
       path = "/etc/app/" + name
       return open(path).read()
