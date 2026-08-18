from sqlmodel import Session

from app.data.model import DerivedFeatures


class DerivedFeaturesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_derived_features(self, derived_features: DerivedFeatures) -> DerivedFeatures:
        self.session.add(derived_features)
        # 거래와 운영 탐지 결과를 한 번에 commit하기 위해 중간 저장만 수행한다.
        self.session.flush()
        self.session.refresh(derived_features)
        return derived_features
