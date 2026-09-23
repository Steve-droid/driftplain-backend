"""Synthetic vulnerable code for separately approved verification; never execute it."""
def lookup(connection, user_input):
    return connection.execute("SELECT name FROM users WHERE id = " + user_input)
