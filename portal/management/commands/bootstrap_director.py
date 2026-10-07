from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from getpass import getpass

from portal.models import User


class Command(BaseCommand):
    help = "Create the initial technical director. Never place a password in command arguments."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--name", required=True)

    def handle(self, *args, **options):
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("Username already exists; no account was changed.")
        password = getpass("كلمة مرور المدير الفني: ")
        user = User(username=options["username"], first_name=options["name"], role=User.Role.DIRECTOR)
        try:
            password_validation.validate_password(password, user)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages))
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS("Director account created."))

