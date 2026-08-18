from sqlmodel import Session

from app.data.model import DerivedFeatures

class DerivedFeaturesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_derived_features(self, derived_features: DerivedFeatures) -> DerivedFeatures:
        self.session.add(derived_features)
        self.session.commit()
        self.session.refresh(derived_features)
        return derived_features