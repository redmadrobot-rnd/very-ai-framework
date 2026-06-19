import subprocess


def run_user_command(user_input: str):
    # DEMO: умышленно небезопасно — исполняет пользовательскую строку в shell.
    return subprocess.run(user_input, shell=True, capture_output=True)
