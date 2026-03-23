path = "webfrontend/htmlauth/content.py"
with open(path, "r") as f:
    py = f.read()

# add rtl_wmbus to dependencies check
py = py.replace("'rtl_test': command_exists('rtl_test'),", "'rtl_test': command_exists('rtl_test'),\n        'rtl_wmbus': command_exists('rtl_wmbus'),")

with open(path, "w") as f:
    f.write(py)
