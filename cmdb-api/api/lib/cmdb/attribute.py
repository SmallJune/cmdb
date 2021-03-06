# -*- coding:utf-8 -*- 

from flask import abort
from flask import current_app

from api.extensions import db
from api.lib.cmdb.cache import AttributeCache
from api.lib.cmdb.const import ValueTypeEnum
from api.lib.cmdb.utils import ValueTypeMap
from api.lib.decorator import kwargs_required
from api.models.cmdb import Attribute
from api.models.cmdb import CITypeAttribute
from api.models.cmdb import CITypeAttributeGroupItem
from api.models.cmdb import PreferenceShowAttributes


class AttributeManager(object):
    """
    CI attributes manager
    """

    def __init__(self):
        pass

    @staticmethod
    def get_choice_values(attr_id, value_type):
        choice_table = ValueTypeMap.choice.get(value_type)
        choice_values = choice_table.get_by(fl=["value"], attr_id=attr_id)
        return [choice_value["value"] for choice_value in choice_values]

    @staticmethod
    def _add_choice_values(_id, value_type, choice_values):
        choice_table = ValueTypeMap.choice.get(value_type)

        db.session.query(choice_table).filter(choice_table.attr_id == _id).delete()
        db.session.flush()
        choice_values = choice_values
        for v in choice_values:
            table = choice_table(attr_id=_id, value=v)
            db.session.add(table)
        db.session.flush()

    @staticmethod
    def _del_choice_values(_id, value_type):
        choice_table = ValueTypeMap.choice.get(value_type)

        db.session.query(choice_table).filter(choice_table.attr_id == _id).delete()
        db.session.flush()

    @classmethod
    def search_attributes(cls, name=None, alias=None, page=1, page_size=None):
        """
        :param name: 
        :param alias: 
        :param page: 
        :param page_size: 
        :return: attribute, if name is None, then return all attributes
        """
        if name is not None:
            attrs = Attribute.get_by_like(name=name)
        elif alias is not None:
            attrs = Attribute.get_by_like(alias=alias)
        else:
            attrs = Attribute.get_by()

        numfound = len(attrs)
        attrs = attrs[(page - 1) * page_size:][:page_size]
        res = list()
        for attr in attrs:
            attr["is_choice"] and attr.update(dict(choice_value=cls.get_choice_values(attr["id"], attr["value_type"])))
            res.append(attr)

        return numfound, res

    def get_attribute_by_name(self, name):
        attr = Attribute.get_by(name=name, first=True)
        if attr and attr["is_choice"]:
            attr.update(dict(choice_value=self.get_choice_values(attr["id"], attr["value_type"])))
        return attr

    def get_attribute_by_alias(self, alias):
        attr = Attribute.get_by(alias=alias, first=True)
        if attr and attr["is_choice"]:
            attr.update(dict(choice_value=self.get_choice_values(attr["id"], attr["value_type"])))
        return attr

    def get_attribute_by_id(self, _id):
        attr = Attribute.get_by_id(_id).to_dict()
        if attr and attr["is_choice"]:
            attr.update(dict(choice_value=self.get_choice_values(attr["id"], attr["value_type"])))
        return attr

    def get_attribute(self, key):
        attr = AttributeCache.get(key).to_dict()
        if attr and attr["is_choice"]:
            attr.update(dict(choice_value=self.get_choice_values(attr["id"], attr["value_type"])))
        return attr

    @classmethod
    @kwargs_required("name")
    def add(cls, **kwargs):
        choice_value = kwargs.pop("choice_value", [])
        kwargs.pop("is_choice", None)
        is_choice = True if choice_value else False
        name = kwargs.pop("name")
        alias = kwargs.pop("alias", "")
        alias = name if not alias else alias
        Attribute.get_by(name=name, first=True) and abort(400, "attribute name <{0}> is duplicated".format(name))
        Attribute.get_by(alias=alias, first=True) and abort(400, "attribute alias <{0}> is duplicated".format(name))

        attr = Attribute.create(flush=True,
                                name=name,
                                alias=alias,
                                is_choice=is_choice,
                                **kwargs)

        if choice_value:
            cls._add_choice_values(attr.id, attr.value_type, choice_value)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error("add attribute error, {0}".format(str(e)))
            return abort(400, "add attribute <{0}> failed".format(name))

        AttributeCache.clean(attr)

        if current_app.config.get("USE_ES"):
            from api.extensions import es
            other = dict()
            other['index'] = True if attr.is_index else False
            if attr.value_type == ValueTypeEnum.TEXT:
                other['analyzer'] = 'ik_max_word'
                other['search_analyzer'] = 'ik_smart'
                if attr.is_index:
                    other["fields"] = {
                        "keyword": {
                            "type": "keyword",
                            "ignore_above": 256
                        }
                    }
            es.update_mapping(name, ValueTypeMap.es_type[attr.value_type], other)

        return attr.id

    def update(self, _id, **kwargs):
        attr = Attribute.get_by_id(_id) or abort(404, "Attribute <{0}> does not exist".format(_id))

        if kwargs.get("name"):
            other = Attribute.get_by(name=kwargs['name'], first=True, to_dict=False)
            if other and other.id != attr.id:
                return abort(400, "Attribute name <{0}> cannot be duplicate!".format(kwargs['name']))
        if kwargs.get("alias"):
            other = Attribute.get_by(alias=kwargs['alias'], first=True, to_dict=False)
            if other and other.id != attr.id:
                return abort(400, "Attribute alias <{0}> cannot be duplicate!".format(kwargs['alias']))

        choice_value = kwargs.pop("choice_value", False)
        is_choice = True if choice_value else False
        kwargs['is_choice'] = is_choice

        attr.update(flush=True, **kwargs)

        if is_choice:
            self._add_choice_values(attr.id, attr.value_type, choice_value)
        else:
            self._del_choice_values(attr.id, attr.value_type)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error("update attribute error, {0}".format(str(e)))
            return abort(400, "update attribute <{0}> failed".format(_id))

        AttributeCache.clean(attr)

        return attr.id

    @staticmethod
    def delete(_id):
        attr = Attribute.get_by_id(_id) or abort(404, "Attribute <{0}> does not exist".format(_id))
        name = attr.name

        if attr.is_choice:
            choice_table = ValueTypeMap.choice.get(attr.value_type)
            db.session.query(choice_table).filter(choice_table.attr_id == _id).delete()  # FIXME: session conflict
            db.session.flush()

        AttributeCache.clean(attr)

        attr.soft_delete()

        for i in CITypeAttribute.get_by(attr_id=_id, to_dict=False):
            i.soft_delete()

        for i in PreferenceShowAttributes.get_by(attr_id=_id, to_dict=False):
            i.soft_delete()

        for i in CITypeAttributeGroupItem.get_by(attr_id=_id, to_dict=False):
            i.soft_delete()

        return name
