import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from easy_qi import Robot

logger = logging.getLogger(__name__)

IPS = [f"172.20.10.{i}" for i in range(2, 3 + 1)]
BEHAVIORS = (
    "animations/Stand/Gestures/BowShort_1",
    "animations/Stand/Gestures/Hey_1",
)


def run_parallel[T](
    ips: list[str], action: Callable[[str], T], action_name: str
) -> dict[str, T]:
    results: dict[str, T] = {}
    failed_ips: list[str] = []

    def run_action(ip: str) -> T:
        logger.info("机器人 %s 开始%s", ip, action_name)
        return action(ip)

    with ThreadPoolExecutor(max_workers=len(ips), thread_name_prefix="nao") as executor:
        future_to_ip = {executor.submit(run_action, ip): ip for ip in ips}
        for future in as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                results[ip] = future.result()
                logger.info("机器人 %s 完成%s", ip, action_name)
            except Exception:
                failed_ips.append(ip)
                logger.exception("机器人 %s %s失败", ip, action_name)

    if failed_ips:
        raise RuntimeError(
            f"以下机器人{action_name}失败：{', '.join(sorted(failed_ips))}"
        )
    return results


def prepare(robot: Robot) -> None:
    robot.motion.wakeUp()
    robot.posture.goToPosture("Crouch", 0.5)


def go_on_stage(robot: Robot) -> None:
    robot.motion.wakeUp()
    robot.posture.goToPosture("Stand", 0.5)


def play_taiji(robot: Robot) -> None:
    robot.play_movement("taiji")


def play_little_apple(robot: Robot) -> None:
    robot.play_movement("littleapple")


def main() -> None:
    robots: dict[str, Robot] = {}
    try:
        robots = run_parallel(IPS, Robot, "连接")

        ips = list(robots)

        run_parallel(
            ips,
            lambda ip: prepare(robots[ip]),
            "准备",
        )

        input("所有机器人已准备好，上台后请按回车...")

        run_parallel(
            ips,
            lambda ip: go_on_stage(robots[ip]),
            "上台",
        )

        input("所有机器人已就位，按回车开始太极...")

        # run_parallel(j

        run_parallel(
            ips,
            lambda ip: play_little_apple(robots[ip]),
            "小苹果",
        )

        while True:
            for behavior in BEHAVIORS:
                run_parallel(
                    ips,
                    lambda ip, name=behavior: robots[ip].play_behavior(name),
                    f"行为 {behavior}",
                )
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，停止执行")
    finally:
        if robots:
            try:
                run_parallel(
                    list(robots), lambda ip: robots[ip].stop_all(), "停止所有动作"
                )
            finally:
                run_parallel(list(robots), lambda ip: robots[ip].motion.rest(), "休息")

    logger.info("全部机器人已休息，程序结束")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )
    main()
