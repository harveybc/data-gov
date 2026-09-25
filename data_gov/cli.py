import argparse


def parse_args():
    parser = argparse.ArgumentParser(
        description="data-gov: governance for multiple data lakes."
    )
    parser.add_argument("--load_config", type=str, help="JSON config path.")
    parser.add_argument("--save_config", type=str, help="Write merged config here.")
    parser.add_argument("--pipeline_plugin", type=str)
    parser.add_argument("--web_plugin", type=str)
    parser.add_argument("--access_plugin", type=str)
    parser.add_argument("--accounting_plugin", type=str)
    parser.add_argument("--role_plugin", type=str)
    parser.add_argument("--web_host", type=str)
    parser.add_argument("--web_port", type=int)
    parser.add_argument("--quiet_mode", action="store_true")
    return parser.parse_known_args()
