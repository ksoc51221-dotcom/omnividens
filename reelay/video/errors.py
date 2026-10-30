"""Errors raised by the video pipeline."""


class VideoError(Exception):
    """Base class. `user_message` is safe to show in Telegram."""

    user_message = "I couldn't process that video."


class DownloadError(VideoError):
    """The link could not be downloaded (private, geo-blocked, unsupported site...)."""

    user_message = (
        "I couldn't download that video. Please send a screen recording "
        "of it instead."
    )


class NetworkError(DownloadError):
    """We couldn't reach the host (DNS, SSL, blocked network, timeout)."""

    def __init__(self, message: str, host: str = ""):
        super().__init__(message)
        where = host or "that site"
        self.user_message = (
            f"I couldn't reach {where} from this network. "
            "Try again on another network (e.g. phone hotspot), or send a screen recording."
        )


class FrameError(VideoError):
    """ffmpeg could not read frames from the file."""

    user_message = "I couldn't read that video file. Try sending it as an mp4."


class TooLongError(DownloadError):
    """Video is over the size or length cap."""

    user_message = (
        "That video is too long or too big for me (max 10 minutes). "
        "Send a shorter clip or a screen recording of the key part."
    )
