from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class Repository:
    def __init__(self,url):
        self.engine=create_engine(url,pool_pre_ping=True)
        self.session=sessionmaker(self.engine,expire_on_commit=False)
