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


class FrameError(VideoError):
    """ffmpeg could not read frames from the file."""

    user_message = "I couldn't read that video file. Try sending it as an mp4."
