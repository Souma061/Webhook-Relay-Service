# Import all ORM models here so SQLAlchemy's mapper can resolve
# cross-model relationships (e.g. Route.endpoint = relationship("Endpoint"))
# regardless of the import order in application code.
#
# Any module that imports a single model must still be able to find all
# related models already registered.  Putting them all here is the
# canonical solution: import this package before configuring the engine.
from app.models.endpoint import Endpoint          # noqa: F401
from app.models.event import Event                # noqa: F401
from app.models.route import Route                # noqa: F401
from app.models.delivery_attempt import DeliveryAttempt  # noqa: F401
