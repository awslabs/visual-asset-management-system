import {EntityPropTypes} from "./EntityPropTypes";

export default function DatabaseEntity(props) {
    const {databaseId, description} = props;
    this.databaseId = databaseId;
    this.description = description;
}

DatabaseEntity.propTypes = {
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
};
