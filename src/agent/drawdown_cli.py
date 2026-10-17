"""Sampled account drawdown watchdog; no strategy launch or automatic flatten."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

from src.agent.service import make_service
from src.agent.drawdown import DrawdownError


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('demo', 'live'), default='demo')
    commands = p.add_subparsers(dest='command', required=True)
    for name in ('status', 'poll', 'set-limit', 'initialize', 'reset-baseline', 'resume', 'reconcile', 'watch'):
        command = commands.add_parser(name)
        command.add_argument('--mode', choices=('demo', 'live'), default=argparse.SUPPRESS)
        if name == 'set-limit':
            command.add_argument('--percent', type=float, required=True)
        if name in ('initialize', 'reset-baseline', 'resume', 'reconcile'):
            command.add_argument('--confirm', action='store_true')
        if name == 'reset-baseline':
            command.add_argument('--reason', default='')
        if name == 'reconcile':
            command.add_argument('--order-link-id', required=True)
        if name == 'watch':
            command.add_argument('--interval', type=float, default=5.)
            command.add_argument('--iterations', type=int)
    return p


def emit(status: dict) -> None:
    print(json.dumps(status, allow_nan=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        service = make_service(args.mode)
        if args.command == 'watch':
            if not math.isfinite(args.interval) or args.interval <= 0 or (args.iterations is not None and args.iterations <= 0):
                raise ValueError('interval and iterations must be positive and finite')
            count, failed = 0, False
            while args.iterations is None or count < args.iterations:
                observed = service.poll_drawdown()
                emit(observed)
                failed = failed or observed['reason'].startswith('drawdown observation failed')
                count += 1
                if args.iterations is None or count < args.iterations:
                    time.sleep(args.interval)
            return int(failed)
        if args.command == 'set-limit':
            service.save_drawdown_limit(args.percent / 100)
            out = service.drawdown_status()
        elif args.command == 'initialize':
            out = service.initialize_drawdown(confirm=args.confirm)
        elif args.command == 'reset-baseline':
            out = service.reset_drawdown_baseline(confirm=args.confirm, reason=args.reason)
        elif args.command == 'reconcile':
            out = service.reconcile_drawdown_order(args.order_link_id, confirm=args.confirm)
        elif args.command == 'resume':
            out = service.resume_drawdown(confirm=args.confirm)
        else:
            out = service.poll_drawdown() if args.command == 'poll' else service.drawdown_status()
        emit(out)
        return int(out['limit_fraction'] is None or (args.command == 'poll' and out['reason'].startswith('drawdown observation failed')))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Do not print arbitrary transport errors, which could contain authentication material.
        message = str(exc) if isinstance(exc, (ValueError, DrawdownError)) else type(exc).__name__
        print(json.dumps({'error': message, 'blocked': True}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
