from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.models import User
from apps.monitors.models import Monitor
from apps.organizations.models import Membership, Organization
from apps.projects.models import Project
from common.constants import Role

PASSWORD = "Sup3r-Secret-Passw0rd!"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    email_verified_at = factory.LazyFunction(timezone.now)

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        self.set_password(extracted or PASSWORD)
        if create:
            self.save(update_fields=["password"])


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Org {n}")
    slug = factory.Sequence(lambda n: f"org-{n}")
    owner = factory.SubFactory(UserFactory)

    @factory.post_generation
    def owner_membership(self, create, extracted, **kwargs):
        if create:
            Membership.objects.get_or_create(organization=self, user=self.owner, defaults={"role": Role.OWNER})


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    role = Role.DEVELOPER


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Project {n}")
    slug = factory.Sequence(lambda n: f"project-{n}")
    environment = "production"


class MonitorFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Monitor

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"Monitor {n}")
    url = "https://example.com/health"
    interval_seconds = 60
    timeout_seconds = 10
    failure_threshold = 3
    recovery_threshold = 2
    next_check_at = factory.LazyFunction(lambda: timezone.now() - timedelta(seconds=1))
